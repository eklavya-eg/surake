import json
from typing import Any, NotRequired, TypedDict
from uuid import UUID

import httpx
from langgraph.graph import END, START, StateGraph

from surake.app.core.config import Settings
from surake.app.db.repository import Repository
from surake.app.models.domain import TodoStatus, WorkerResult


class WorkerState(TypedDict):
    project_id: UUID
    task_id: UUID
    todo: dict[str, Any]
    context: NotRequired[dict[str, Any]]
    agent_type: NotRequired[str]
    agent_result: NotRequired[dict[str, Any]]
    result: NotRequired[WorkerResult]


class BoundedTaskWorker:
    def __init__(self, repository: Repository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings
        graph = StateGraph(WorkerState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("route", self._route)
        graph.add_node("execute", self._execute)
        graph.add_node("validate", self._validate)
        graph.add_node("commit", self._commit)
        graph.add_edge(START, "load_context")
        graph.add_edge("load_context", "route")
        graph.add_edge("route", "execute")
        graph.add_edge("execute", "validate")
        graph.add_edge("validate", "commit")
        graph.add_edge("commit", END)
        self.graph = graph.compile()

    async def run(self, project_id: UUID, task_id: UUID, todo: dict[str, Any]) -> WorkerResult:
        output = await self.graph.ainvoke(
            {"project_id": project_id, "task_id": task_id, "todo": todo}
        )
        result = output.get("result")
        if result is None:
            raise RuntimeError("worker graph finished without a result")
        return result

    async def _load_context(self, state: WorkerState) -> dict[str, dict[str, Any]]:
        context = await self.repository.bounded_worker_context(
            state["project_id"], state["task_id"]
        )
        return {"context": context}

    async def _route(self, state: WorkerState) -> dict[str, str]:
        description = state["todo"]["description"].lower()
        if any(term in description for term in ("browse", "website", "web ", "url", "search online")):
            return {"agent_type": "browser"}
        if any(term in description for term in ("pdf", "document", "form")):
            return {"agent_type": "pdf_editor"}
        return {"agent_type": "general"}

    async def _execute(self, state: WorkerState) -> dict[str, dict[str, Any]]:
        todo = state["todo"]
        agent_type = state.get("agent_type")
        if agent_type is None:
            raise RuntimeError("worker graph did not select an agent")
        if agent_type == "general":
            return {
                "agent_result": {
                    "status": "completed",
                    "result_summary": f"Accepted bounded work item: {todo['description']}",
                    "evidence": [],
                    "artifacts": [],
                    "blockers": [],
                }
            }

        url = self.settings.browser_agent_url if agent_type == "browser" else self.settings.pdf_editor_agent_url
        context = state.get("context", {})
        focused_context = {
            "durable_context": context.get("durable_context", []),
            "available_artifacts": context.get("available_artifacts", []),
        }
        instruction = (
            f"{todo['description']}\n\nUse these durable project facts and preferences across this task: "
            f"{json.dumps(focused_context, default=str)}"
        )
        request_payload = {
            "project_id": str(state["project_id"]),
            "task_id": str(state["task_id"]),
            "todo_id": str(todo["todo_id"]),
            "instruction": instruction,
        }
        if agent_type == "pdf_editor" and todo.get("artifact_refs"):
            request_payload["source_artifact"] = todo["artifact_refs"][0]
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{url}/v1/tasks", json=request_payload)
            response.raise_for_status()
            return {"agent_result": response.json()}

    async def _validate(self, state: WorkerState) -> dict[str, WorkerResult]:
        todo = state["todo"]
        raw = state.get("agent_result")
        if raw is None:
            raise RuntimeError("subagent returned no result")
        status = TodoStatus(raw.get("status", "failed"))
        if status not in {TodoStatus.COMPLETED, TodoStatus.BLOCKED, TodoStatus.FAILED}:
            raise ValueError("subagent returned a non-terminal bounded result")
        result = WorkerResult(
            execution_id=todo["execution_id"],
            task_id=state["task_id"],
            todo_id=todo["todo_id"],
            claimed_version=todo["claimed_version"],
            status=status,
            result_summary=raw.get("result_summary", ""),
            evidence=raw.get("evidence", []),
            artifacts=raw.get("artifacts", []),
            blockers=raw.get("blockers", []),
        )
        return {"result": result}

    async def _commit(self, state: WorkerState) -> dict[str, WorkerResult]:
        result = state.get("result")
        if result is None:
            raise RuntimeError("validated worker result is missing")
        await self.repository.commit_worker_result(result)
        return {"result": result}
