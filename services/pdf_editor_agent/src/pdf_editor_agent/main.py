import hashlib
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from pypdf import PdfReader, PdfWriter

ARTIFACT_ROOT = Path(os.getenv("PDF_ARTIFACT_ROOT", "artifacts")).resolve()
ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)


class PdfTask(BaseModel):
    project_id: UUID
    task_id: UUID
    todo_id: UUID
    instruction: str = Field(min_length=1, max_length=10_000)
    source_artifact: str | None = None


class AgentResult(BaseModel):
    status: str
    result_summary: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


app = FastAPI(title="Surake PDF Editor Agent", version="0.1.0")


def _safe_source(artifact_ref: str) -> Path:
    source = (ARTIFACT_ROOT / artifact_ref).resolve()
    if ARTIFACT_ROOT not in source.parents or not source.is_file():
        raise HTTPException(status_code=404, detail="source artifact not found")
    return source


def _write_artifact(project_id: UUID, name: str, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()[:16]
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "edited.pdf"
    directory = (ARTIFACT_ROOT / str(project_id)).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}-{safe_name}"
    target.write_bytes(content)
    return str(target.relative_to(ARTIFACT_ROOT)).replace("\\", "/")


def _rotate_pdf(content: bytes, degrees: int) -> bytes:
    if degrees not in {0, 90, 180, 270}:
        raise ValueError("rotation must be 0, 90, 180, or 270 degrees")
    reader = PdfReader(BytesIO(content))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page.rotate(degrees))
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _merge_pdfs(contents: list[bytes]) -> bytes:
    writer = PdfWriter()
    for content in contents:
        reader = PdfReader(BytesIO(content))
        for page in reader.pages:
            writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "agent": "pdf_editor", "artifacts": "local"}


@app.post("/v1/tasks", response_model=AgentResult)
async def run_task(task: PdfTask) -> AgentResult:
    if not task.source_artifact:
        return AgentResult(
            status="blocked",
            result_summary="PDF editing requires a source_artifact reference.",
            blockers=["source_artifact was not provided"],
        )
    source = _safe_source(task.source_artifact)
    rotation = re.search(r"rotate(?: by)?\s+(90|180|270)", task.instruction.lower())
    if rotation is None:
        return AgentResult(
            status="blocked",
            result_summary="The requested PDF operation is not supported by the bounded editor.",
            blockers=["Supported agent instruction: rotate by 90, 180, or 270 degrees"],
        )
    degrees = int(rotation.group(1))
    edited = _rotate_pdf(source.read_bytes(), degrees)
    artifact = _write_artifact(task.project_id, f"rotated-{degrees}.pdf", edited)
    return AgentResult(
        status="completed",
        result_summary=f"Rotated the PDF by {degrees} degrees.",
        artifacts=[artifact],
        evidence=[{"operation": "rotate", "degrees": degrees}],
    )


@app.post("/v1/pdf/rotate", response_model=AgentResult)
async def rotate_pdf(
    project_id: Annotated[UUID, Form()],
    degrees: Annotated[int, Form()],
    file: Annotated[UploadFile, File()],
) -> AgentResult:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="file must be a PDF")
    try:
        edited = _rotate_pdf(await file.read(), degrees)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    artifact = _write_artifact(project_id, f"rotated-{degrees}.pdf", edited)
    return AgentResult(
        status="completed",
        result_summary=f"Rotated the PDF by {degrees} degrees.",
        artifacts=[artifact],
    )


@app.post("/v1/pdf/merge", response_model=AgentResult)
async def merge_pdf(
    project_id: Annotated[UUID, Form()],
    files: Annotated[list[UploadFile], File()],
) -> AgentResult:
    if len(files) < 2:
        raise HTTPException(status_code=422, detail="at least two PDFs are required")
    if any(file.content_type != "application/pdf" for file in files):
        raise HTTPException(status_code=415, detail="all files must be PDFs")
    merged = _merge_pdfs([await file.read() for file in files])
    artifact = _write_artifact(project_id, "merged.pdf", merged)
    return AgentResult(
        status="completed",
        result_summary=f"Merged {len(files)} PDF files.",
        artifacts=[artifact],
    )


def main() -> None:
    import uvicorn

    uvicorn.run("pdf_editor_agent.main:app", host="127.0.0.1", port=8102)
