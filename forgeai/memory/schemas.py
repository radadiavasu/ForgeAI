"""Pydantic schemas for Agent_Memory, Task_Memory, and checkpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Lesson(BaseModel):
    """A learned remediation keyed by agent role (not agent instance)."""

    id: str
    agent_role: str
    failure_description: str
    root_cause: str
    resolution: str
    rule: str
    created_at: datetime
    project_id: str
    task_id: str
    confidence: str = "high"
    human_verified: bool = False
    resolved_at_escalation_level: int = 4
    health_score: float = 1.0
    total_uses: int = 0
    success_count: int = 0
    fail_count: int = 0
    flagged: bool = False
    flag_reason: str = ""
    context_guards: dict[str, str] = Field(default_factory=dict)
    supersedes: str | None = None


class LessonQueryResult(BaseModel):
    """Lesson plus semantic similarity score."""

    lesson: Lesson
    relevance_score: float = Field(ge=0.0, le=1.0)


class TaskMemoryEntry(BaseModel):
    """Optional envelope for stored task-scoped values."""

    key: str
    value: str


class TaskCheckpointMeta(BaseModel):
    """Metadata about a stored checkpoint object."""

    object_path: str
    task_id: str
    agent_id: str
