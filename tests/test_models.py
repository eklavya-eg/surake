from uuid import uuid4

import pytest
from pydantic import ValidationError

from surake.app.models.domain import (
    DecisionAction,
    DecisionProposal,
    DecisionSnapshot,
    ImpactType,
    InvokeRequest,
)


def test_existing_task_decision_requires_task_id() -> None:
    with pytest.raises(ValidationError):
        DecisionProposal(
            action=DecisionAction.CONTINUE_TASK,
            impact_type=ImpactType.ADDED_REQUIREMENT,
            reason="Matches existing work",
            confidence=0.8,
            snapshot=DecisionSnapshot(project_version=1, task_version=1),
        )


def test_project_creation_rejects_existing_project_id() -> None:
    with pytest.raises(ValidationError):
        InvokeRequest(
            user_query="Start work",
            owner_id="owner",
            project_id=uuid4(),
            create_project=True,
        )
