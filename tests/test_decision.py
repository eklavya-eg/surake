from uuid import uuid4

import pytest

from surake.app.models.domain import DecisionAction
from surake.app.services.decision import JevDecisionEngine


@pytest.mark.asyncio
async def test_fallback_creates_task_without_matching_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    engine = JevDecisionEngine()
    project_id = uuid4()
    proposal = await engine.decide(
        "Build a project roadmap",
        {
            "project": {"project_id": project_id, "version": 3, "project_map": ""},
            "tasks": [],
        },
    )
    assert proposal.action is DecisionAction.CREATE_TASK
    assert proposal.snapshot.project_version == 3
    assert proposal.todos == ["Build a project roadmap"]


@pytest.mark.asyncio
async def test_fallback_reuses_matching_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    engine = JevDecisionEngine()
    task_id = uuid4()
    proposal = await engine.decide(
        "Research competitor pricing online",
        {
            "project": {"project_id": uuid4(), "version": 2, "project_map": ""},
            "tasks": [
                {
                    "task_id": task_id,
                    "title": "Research competitor products",
                    "description": "Collect competitor pricing and features",
                    "task_summary": "",
                    "status": "ready",
                    "priority": 0,
                    "version": 5,
                    "active_execution_id": None,
                }
            ],
        },
    )
    assert proposal.action is DecisionAction.CONTINUE_TASK
    assert proposal.task_id == task_id
    assert proposal.snapshot.task_version == 5
