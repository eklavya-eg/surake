from fastapi import APIRouter, Request

from surake.app.db.repository import Repository

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    repository: Repository = request.app.state.repository
    await repository.pool.fetchval("SELECT 1")
    return {"status": "ok", "database": "postgresql", "artifacts": "local"}


@router.post("/maintenance/recover-leases")
async def recover_leases(request: Request) -> dict[str, int]:
    repository: Repository = request.app.state.repository
    return {"recovered": await repository.recover_expired_leases()}
