from uuid import UUID, uuid4

from surake.app.db.repository import Repository, VersionConflictError
from surake.app.models.domain import DecisionAction, InvokeRequest, InvokeResponse
from surake.app.services.decision import DecisionEngine
from surake.app.workers.graph import BoundedTaskWorker


class ProjectOrchestrator:
    def __init__(
        self,
        repository: Repository,
        decision_engine: DecisionEngine,
        worker: BoundedTaskWorker,
    ) -> None:
        self.repository = repository
        self.decision_engine = decision_engine
        self.worker = worker

    async def invoke(self, payload: InvokeRequest) -> InvokeResponse:
        request_id = payload.request_id or uuid4()
        if not await self.repository.create_request(payload, request_id):
            saved = await self.repository.get_request_response(request_id, payload.owner_id)
            if saved is not None:
                return InvokeResponse.model_validate({**saved, "duplicate": True})
            project_id = payload.project_id or UUID(int=0)
            return InvokeResponse(
                request_id=request_id,
                project_id=project_id,
                status="processing",
                action=DecisionAction.QUEUE_UNTIL_IDLE,
                detail="An identical request is already being processed.",
                duplicate=True,
            )

        project = await self.repository.resolve_project(payload)
        project_id = project["project_id"]
        await self.repository.attach_request_to_project(request_id, project_id)
        await self.repository.save_initiation_context(
            project_id,
            payload.owner_id,
            payload.project_context,
            payload.preferences,
            request_id,
        )

        task_id: UUID | None = None
        proposal = None
        for attempt in range(2):
            snapshot = await self.repository.project_snapshot(project_id, payload.owner_id)
            proposal = await self.decision_engine.decide(payload.user_query, snapshot)
            try:
                task_id, _ = await self.repository.apply_decision(
                    request_id, project_id, proposal, payload.user_query
                )
                break
            except VersionConflictError:
                if attempt == 1:
                    raise

        if proposal is None:
            raise RuntimeError("decision engine returned no proposal")

        execution_id: UUID | None = None
        detail = proposal.reason
        status = "accepted"
        if task_id is not None and proposal.action in {
            DecisionAction.CREATE_TASK,
            DecisionAction.CONTINUE_TASK,
        }:
            claimed = await self.repository.claim_next_todo(
                project_id, task_id, request_id, worker_id=f"api:{request_id}"
            )
            if claimed is not None:
                execution_id = claimed["execution_id"]
                result = await self.worker.run(project_id, task_id, claimed)
                detail = result.result_summary
                status = result.status
            else:
                status = "queued"
        elif proposal.action is DecisionAction.REQUEST_STEERING:
            status = "steering_pending"

        response = InvokeResponse(
            request_id=request_id,
            project_id=project_id,
            task_id=task_id,
            execution_id=execution_id,
            status=str(status),
            action=proposal.action,
            detail=detail,
        )
        await self.repository.save_response(response)
        return response
