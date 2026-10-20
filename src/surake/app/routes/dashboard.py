from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter(include_in_schema=False)
DASHBOARD = Path(__file__).parents[1] / "ui" / "dashboard.html"


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/ui")


@router.get("/ui", response_class=FileResponse)
async def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD, media_type="text/html")
