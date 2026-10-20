from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TodoStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class RequestStatus(StrEnum):
    RECEIVED = "received"
    CLASSIFYING = "classifying"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    WAITING_FOR_USER = "waiting_for_user"


class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DecisionAction(StrEnum):
    CONTINUE_TASK = "continue_task"
    CREATE_TASK = "create_task"
    MODIFY_TASK = "modify_task"
    INFORMATIONAL_ANSWER = "informational_answer"
    ASK_USER = "ask_user"
    QUEUE_UNTIL_IDLE = "queue_until_idle"
    REQUEST_STEERING = "request_steering"
    NEW_PROJECT = "new_project"


class ImpactType(StrEnum):
    CREATED = "created"
    CLARIFIED = "clarified"
    REPRIORITIZED = "reprioritized"
    ADDED_REQUIREMENT = "added_requirement"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    INFORMATIONAL = "informational"
    UNRELATED = "unrelated"


class InvokeRequest(BaseModel):
    user_query: str = Field(min_length=1, max_length=20_000)
    owner_id: str = Field(min_length=1, max_length=255)
    request_id: UUID | None = None
    project_id: UUID | None = None
    external_thread_id: str | None = Field(default=None, max_length=255)
    create_project: bool = False
    project_name: str | None = Field(default=None, max_length=255)
    project_context: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_project_creation(self) -> "InvokeRequest":
        if self.create_project and self.project_id is not None:
            raise ValueError("project_id cannot be supplied when create_project is true")
        return self


class DecisionSnapshot(BaseModel):
    project_version: int = Field(ge=1)
    task_version: int | None = Field(default=None, ge=1)


class DecisionProposal(BaseModel):
    action: DecisionAction
    task_id: UUID | None = None
    impact_type: ImpactType
    requires_steering: bool = False
    reason: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    snapshot: DecisionSnapshot
    task_title: str | None = Field(default=None, max_length=500)
    todos: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_task_for_existing_actions(self) -> "DecisionProposal":
        existing = {
            DecisionAction.CONTINUE_TASK,
            DecisionAction.MODIFY_TASK,
            DecisionAction.REQUEST_STEERING,
            DecisionAction.QUEUE_UNTIL_IDLE,
        }
        if self.action in existing and self.task_id is None:
            raise ValueError(f"task_id is required for {self.action}")
        if self.action is DecisionAction.CREATE_TASK and not self.task_title:
            raise ValueError("task_title is required for create_task")
        return self


class WorkerResult(BaseModel):
    execution_id: UUID
    task_id: UUID
    todo_id: UUID
    claimed_version: int = Field(ge=1)
    status: TodoStatus
    result_summary: str = Field(max_length=20_000)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class InvokeResponse(BaseModel):
    request_id: UUID
    project_id: UUID
    task_id: UUID | None = None
    execution_id: UUID | None = None
    status: str
    action: DecisionAction
    detail: str
    duplicate: bool = False


class ProjectView(BaseModel):
    project_id: UUID
    owner_id: str
    name: str
    project_map: str
    version: int
    status: str
    created_at: datetime
    updated_at: datetime


class TaskView(BaseModel):
    task_id: UUID
    project_id: UUID
    title: str
    description: str
    task_summary: str
    status: TaskStatus
    priority: int
    version: int
    created_at: datetime
    updated_at: datetime


class ContextCategory(StrEnum):
    GOAL = "goal"
    PREFERENCE = "preference"
    CONSTRAINT = "constraint"
    FACT = "fact"


class ProjectContextUpsert(BaseModel):
    key: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.-]+$")
    value: Any
    category: ContextCategory = ContextCategory.FACT
    importance: int = Field(default=5, ge=1, le=10)


class ArtifactKind(StrEnum):
    RESUME_BASE = "resume_base"
    RESUME_VARIANT = "resume_variant"
    JOB_DESCRIPTION = "job_description"
    COVER_LETTER = "cover_letter"
    GENERAL = "general"


class TodoView(BaseModel):
    todo_id: UUID
    task_id: UUID
    description: str
    requirements: list[str]
    acceptance_criteria: list[str]
    status: TodoStatus
    position: int
    depends_on: list[UUID]
    version: int
    result_summary: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
