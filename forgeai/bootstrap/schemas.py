"""Bootstrap protocol Pydantic models (Phase 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from forgeai.llm.schemas import MasterDocument, TechStackDocument


class TaskSpec(BaseModel):
    title: str
    description: str = ""
    complexity: str  # LOW/MEDIUM/HIGH
    phase: str  # FRONTEND_PHASE/BACKEND_PHASE
    dependencies: list[str] = Field(default_factory=list)


class TaskPlan(BaseModel):
    frontend_tasks: list[TaskSpec] = Field(default_factory=list)
    backend_tasks: list[TaskSpec] = Field(default_factory=list)
    total_tasks: int = 0
    estimated_complexity_distribution: dict[str, int] = Field(default_factory=dict)


class AgentRecommendation(BaseModel):
    frontend_agent_count: int
    backend_agent_count: int
    qa_agent_count: int
    reasoning: str
    time_estimate_minutes: int
    cost_estimate_usd: float


class ApprovedConfig(BaseModel):
    frontend_agent_count: int
    backend_agent_count: int
    qa_agent_count: int
    approved_by: str = "human"
    approved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BootstrapResult(BaseModel):
    master_document: MasterDocument
    tech_stack_document: TechStackDocument
    task_plan: TaskPlan
    agents_created: list[str]
    recommendation: AgentRecommendation
