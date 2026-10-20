import hashlib
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse

from surake.app.core.config import Settings
from surake.app.db.repository import Repository
from surake.app.models.domain import ArtifactKind, ProjectContextUpsert
from surake.app.services.artifacts import LocalArtifactStore

router = APIRouter()


@router.get("/projects")
async def list_projects(
    request: Request,
    owner_id: Annotated[str, Query(min_length=1, max_length=255)],
) -> list[dict[str, Any]]:
    repository: Repository = request.app.state.repository
    return await repository.list_projects(owner_id)


@router.get("/projects/{project_id}/observability")
async def project_observability(
    project_id: UUID,
    request: Request,
    owner_id: Annotated[str, Query(min_length=1, max_length=255)],
) -> dict[str, Any]:
    repository: Repository = request.app.state.repository
    return await repository.observability_snapshot(project_id, owner_id)


@router.post("/projects/{project_id}/context")
async def save_project_context(
    project_id: UUID,
    payload: ProjectContextUpsert,
    request: Request,
    owner_id: Annotated[str, Query(min_length=1, max_length=255)],
) -> dict[str, Any]:
    repository: Repository = request.app.state.repository
    return await repository.upsert_project_context(
        project_id,
        owner_id,
        payload.key,
        payload.value,
        payload.category,
        payload.importance,
    )


@router.post(
    "/projects/{project_id}/artifacts",
    status_code=status.HTTP_201_CREATED,
)
async def upload_artifact(
    project_id: UUID,
    request: Request,
    owner_id: Annotated[str, Form(min_length=1, max_length=255)],
    artifact_kind: Annotated[ArtifactKind, Form()],
    file: Annotated[UploadFile, File()],
    label: Annotated[str | None, Form(max_length=500)] = None,
    task_id: Annotated[UUID | None, Form()] = None,
    parent_artifact_id: Annotated[UUID | None, Form()] = None,
    target_jd_artifact_id: Annotated[UUID | None, Form()] = None,
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    content = await file.read(settings.max_artifact_size_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_artifact_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"artifact exceeds {settings.max_artifact_size_mb} MB",
        )
    if not content:
        raise HTTPException(status_code=422, detail="artifact is empty")
    store: LocalArtifactStore = request.app.state.artifact_store
    repository: Repository = request.app.state.repository
    name = file.filename or "artifact.bin"
    storage_ref = store.save(project_id, name, content)
    try:
        return await repository.register_artifact(
            project_id=project_id,
            owner_id=owner_id,
            name=label or name,
            artifact_kind=artifact_kind,
            storage_ref=storage_ref,
            media_type=file.content_type or "application/octet-stream",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            metadata={"original_filename": name},
            task_id=task_id,
            parent_artifact_id=parent_artifact_id,
            target_jd_artifact_id=target_jd_artifact_id,
        )
    except Exception:
        store.delete(storage_ref)
        raise


@router.get(
    "/projects/{project_id}/artifacts/{artifact_id}/download",
    response_class=FileResponse,
)
async def download_artifact(
    project_id: UUID,
    artifact_id: UUID,
    request: Request,
    owner_id: Annotated[str, Query(min_length=1, max_length=255)],
) -> FileResponse:
    repository: Repository = request.app.state.repository
    artifact = await repository.get_artifact(artifact_id, project_id, owner_id)
    store: LocalArtifactStore = request.app.state.artifact_store
    try:
        path = store.resolve(artifact["storage_ref"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="artifact file is missing") from exc
    return FileResponse(path, media_type=artifact["media_type"], filename=artifact["name"])
