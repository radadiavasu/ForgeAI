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
