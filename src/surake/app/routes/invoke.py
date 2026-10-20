from fastapi import APIRouter, Request, status

from surake.app.models.domain import InvokeRequest, InvokeResponse
from surake.app.services.orchestrator import ProjectOrchestrator

router = APIRouter()


@router.post("/invoke", response_model=InvokeResponse, status_code=status.HTTP_202_ACCEPTED)
async def invoke_function(payload: InvokeRequest, request: Request) -> InvokeResponse:
    orchestrator: ProjectOrchestrator = request.app.state.orchestrator
    return await orchestrator.invoke(payload)
