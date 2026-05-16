"""Intelligence layer schemas (Phase 9)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class ConfidenceScore(BaseModel):
    score: int = Field(ge=0, le=100)
    agent_id: str
    task_id: str
    rationale: str
    scored_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PeerReviewResult(BaseModel):
    task_id: str
    reviewer_agent_id: str
    approved: bool
    feedback: str
    confidence_in_review: int = Field(ge=0, le=100)
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContextReductionResult(BaseModel):
    original_tokens: int
    final_tokens: int
    reduction_applied: bool
    strategies_used: list[str] = Field(default_factory=list)
    under_limit: bool
    reduced_context: str


class FinalReviewResult(BaseModel):
    project_id: str
    passed: bool
    consistency_checks: list[str] = Field(default_factory=list)
    gaps_found: list[str] = Field(default_factory=list)
    remediation_tasks: list[str] = Field(default_factory=list)
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reviewer: str = "lead_agent"
