import os
import re
from typing import Any, Protocol
from uuid import UUID

from surake.app.models.domain import (
    DecisionAction,
    DecisionProposal,
    DecisionSnapshot,
    ImpactType,
)


class DecisionEngine(Protocol):
    async def decide(self, user_query: str, snapshot: dict[str, Any]) -> DecisionProposal: ...


class JevDecisionEngine:
    def __init__(self) -> None:
        self._classifier: Any | None = None
        if os.getenv("JEV_API_KEY"):
            from langchain_typesafe import TypeSafeClassifier

            self._classifier = TypeSafeClassifier(model="jev-1.13")

    async def decide(self, user_query: str, snapshot: dict[str, Any]) -> DecisionProposal:
        if self._classifier is None:
            return self._deterministic_fallback(user_query, snapshot)

        project = snapshot["project"]
        tasks = snapshot["tasks"]
        response = await self._classifier.ainvoke(
            {
                "state": {
                    "user_request": user_query,
                    "project_summary": project["project_map"],
                    "project_version": project["version"],
                    "active_tasks": [
                        {
                            "id": str(task["task_id"]),
                            "title": task["title"],
                            "summary": task["task_summary"],
                            "status": task["status"],
                            "version": task["version"],
                        }
                        for task in tasks
                    ],
                    "durable_context": snapshot.get("contexts", []),
                    "artifact_index": snapshot.get("artifacts", []),
                    "available_agents": ["browser", "pdf_editor", "general"],
                },
                "questions": {
                    "proposal": {
                        "type": "json",
                        "instructions": (
                            "Return only a proposal matching DecisionProposal. Existing-task actions "
                            "must use one supplied task ID and version. Never invent IDs or statuses."
                        ),
                    }
                },
            }
        )
        raw = response.get("proposal", response) if isinstance(response, dict) else response
        proposal = DecisionProposal.model_validate_json(raw) if isinstance(raw, str) else DecisionProposal.model_validate(raw)
        self._validate_references(proposal, snapshot)
        return proposal

    @staticmethod
    def _validate_references(proposal: DecisionProposal, snapshot: dict[str, Any]) -> None:
        if proposal.task_id is None:
            return
        tasks = {task["task_id"]: task for task in snapshot["tasks"]}
        task = tasks.get(proposal.task_id)
        if task is None:
            raise ValueError("decision referenced a task outside the authorized snapshot")
        if proposal.snapshot.task_version != task["version"]:
            raise ValueError("decision supplied the wrong task snapshot version")

    @staticmethod
    def _deterministic_fallback(user_query: str, snapshot: dict[str, Any]) -> DecisionProposal:
        project = snapshot["project"]
        query_terms = set(re.findall(r"[a-z0-9]+", user_query.lower()))
        best_task: dict[str, Any] | None = None
        best_score = 0
        for task in snapshot["tasks"]:
            task_terms = set(re.findall(r"[a-z0-9]+", f"{task['title']} {task['description']}".lower()))
            score = len(query_terms & task_terms)
            if score > best_score:
                best_score = score
                best_task = task

        if best_task is not None and best_score >= 2:
            running = best_task["status"] == "in_progress"
            return DecisionProposal(
                action=DecisionAction.REQUEST_STEERING if running else DecisionAction.CONTINUE_TASK,
                task_id=UUID(str(best_task["task_id"])),
                impact_type=ImpactType.ADDED_REQUIREMENT,
                requires_steering=running,
                reason="The request shares multiple objective terms with an existing task.",
                confidence=min(0.95, 0.65 + best_score * 0.05),
                snapshot=DecisionSnapshot(
                    project_version=project["version"], task_version=best_task["version"]
                ),
            )

        title = " ".join(user_query.strip().split())[:120]
        return DecisionProposal(
            action=DecisionAction.CREATE_TASK,
            impact_type=ImpactType.CREATED,
            reason="No existing task has enough deterministic term overlap.",
            confidence=0.7,
            snapshot=DecisionSnapshot(project_version=project["version"]),
            task_title=title,
            todos=[user_query],
        )
