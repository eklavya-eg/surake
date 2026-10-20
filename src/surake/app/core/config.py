from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Surake Project Orchestrator"
    database_url: str = "postgresql://surake:surake@localhost:5432/surake"
    artifact_root: Path = Path("artifacts")
    lease_seconds: int = 300
    max_project_workers: int = 4
    max_artifact_size_mb: int = 25
    browser_agent_url: str = "http://localhost:8101"
    pdf_editor_agent_url: str = "http://localhost:8102"


@lru_cache
def get_settings() -> Settings:
    return Settings()
