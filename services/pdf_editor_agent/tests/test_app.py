from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from pdf_editor_agent.main import ARTIFACT_ROOT, app


def make_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_rotate_pdf_writes_local_artifact() -> None:
    project_id = uuid4()
    response = TestClient(app).post(
        "/v1/pdf/rotate",
        data={"project_id": str(project_id), "degrees": "90"},
        files={"file": ("input.pdf", make_pdf(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    artifact_ref = response.json()["artifacts"][0]
    target = ARTIFACT_ROOT / artifact_ref
    assert target.is_file()
    page = PdfReader(target).pages[0]
    assert page.rotation == 90
    target.unlink()


def test_merge_requires_two_pdfs() -> None:
    response = TestClient(app).post(
        "/v1/pdf/merge",
        data={"project_id": str(uuid4())},
        files=[("files", ("one.pdf", make_pdf(), "application/pdf"))],
    )
    assert response.status_code == 422
