"""Pydantic models for tasks and transition payloads."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from forgeai.models.task import TaskComplexity
from forgeai.state_machine.states import TaskState


class TaskRead(BaseModel):
    """Serialized task for API-style interchange (Phase 1 internal use)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    title: str
    description: str | None
    assigned_agent: str
    complexity: TaskComplexity
    current_state: TaskState
    created_at: datetime
    updated_at: datetime
    output: str | None


class TransitionRequest(BaseModel):
    """Optional structured transition payload used by tests and tooling."""

    to_state: TaskState
    agent_id: str = Field(..., min_length=1)
    phase_transition_approval: bool | None = None
    defect_report: str | None = None
    rework_reason: str | None = None
    output: str | None = None
    work_output: str | None = None
