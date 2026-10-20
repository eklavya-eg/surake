from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from surake.app.core.config import get_settings
from surake.app.db.pool import Database
from surake.app.db.repository import AuthorizationError, NotFoundError, Repository, VersionConflictError
from surake.app.routes import dashboard, invoke, projects, system
from surake.app.services.artifacts import LocalArtifactStore
from surake.app.services.decision import JevDecisionEngine
from surake.app.services.orchestrator import ProjectOrchestrator
from surake.app.workers.graph import BoundedTaskWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    database = Database(settings)
    await database.connect()
    await database.initialize_schema()
    repository = Repository(database.require_pool(), settings.lease_seconds)
    worker = BoundedTaskWorker(repository, settings)
    app.state.settings = settings
    app.state.repository = repository
    app.state.artifact_store = LocalArtifactStore(settings.artifact_root)
    app.state.orchestrator = ProjectOrchestrator(repository, JevDecisionEngine(), worker)
    yield
    await database.close()


app = FastAPI(title="Surake Project Orchestrator", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)


@app.exception_handler(NotFoundError)
async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(AuthorizationError)
async def authorization_handler(_: Request, exc: AuthorizationError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(VersionConflictError)
async def version_conflict_handler(_: Request, exc: VersionConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


app.include_router(dashboard.router)
app.include_router(invoke.router, prefix="/api", tags=["invoke"])
app.include_router(projects.router, prefix="/api", tags=["projects"])
app.include_router(system.router, prefix="/api", tags=["system"])

