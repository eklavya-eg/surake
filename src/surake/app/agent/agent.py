from uuid import UUID

from surake.app.models.domain import InvokeRequest, InvokeResponse
from surake.app.services.orchestrator import ProjectOrchestrator


class Agent:
    """Compatibility facade around the authoritative project orchestrator."""

    def __init__(self, orchestrator: ProjectOrchestrator) -> None:
        self.orchestrator = orchestrator

    async def ainvoke(self, request: InvokeRequest) -> InvokeResponse:
        return await self.orchestrator.invoke(request)

    async def run(
        self,
        query: str,
        owner_id: str,
        request_id: UUID | None = None,
        project_id: UUID | None = None,
        external_thread_id: str | None = None,
        create_project: bool = False,
        project_name: str | None = None,
    ) -> InvokeResponse:
        payload = InvokeRequest(
            user_query=query,
            owner_id=owner_id,
            request_id=request_id,
            project_id=project_id,
            external_thread_id=external_thread_id,
            create_project=create_project,
            project_name=project_name,
        )
        return await self.ainvoke(payload)
