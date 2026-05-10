"""Pydantic schemas for escalation and drift-monitor outcomes."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum

from pydantic import BaseModel, Field


class EscalationLevel(IntEnum):
    """Ordered escalation levels for automated recovery flow."""

    SELF_RETRY = 1
    PEER_ASSIST = 2
    ARCHITECT_REVIEW = 3
    TASK_REWRITE = 4
    HUMAN_INPUT = 5


class EscalationEvent(BaseModel):
    """Single escalation attempt record."""

    id: str = ""
    task_id: str
    agent_id: str
    level: EscalationLevel
    error_signature: str
    error_detail: str
    loop_count: int
    timestamp: datetime
    resolved: bool = False
    resolution: str = ""
    needs_human_input: bool = False
    human_message: str = ""


class EscalationResult(BaseModel):
    """Result returned after an escalation sequence."""

    level_reached: EscalationLevel
    resolved: bool
    resolution: str
    needs_human_input: bool
    human_message: str = ""


class DriftCheckResult(BaseModel):
    """Result of semantic drift evaluation for one agent output."""

    score: int = Field(ge=0, le=100)
    is_drifting: bool
    threshold: int
    description: str
