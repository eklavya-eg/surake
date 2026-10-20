from pathlib import Path

import asyncpg

from surake.app.core.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        async def configure_connection(connection: asyncpg.Connection) -> None:
            import json

            for type_name in ("json", "jsonb"):
                await connection.set_type_codec(
                    type_name,
                    encoder=json.dumps,
                    decoder=json.loads,
                    schema="pg_catalog",
                )

        self.pool = await asyncpg.create_pool(
            self.settings.database_url,
            min_size=1,
            max_size=max(2, self.settings.max_project_workers + 1),
            command_timeout=30,
            init=configure_connection,
        )

    async def initialize_schema(self) -> None:
        if self.pool is None:
            raise RuntimeError("database is not connected")
        schema_path = Path(__file__).with_name("schema.sql")
        async with self.pool.acquire() as connection:
            await connection.execute(schema_path.read_text(encoding="utf-8"))

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("database is not connected")
        return self.pool
