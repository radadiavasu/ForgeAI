"""Orchestration schemas for QA loop and human gate (Phase 7)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from forgeai.contracts.schemas import ComponentEntry
from forgeai.escalation.schemas import EscalationResult


class DefectReport(BaseModel):
    task_id: str
    agent_id: str
    original_agent_id: str
    failure_summary: str
    failed_tests: list[str] = Field(default_factory=list)
    passed_tests: list[str] = Field(default_factory=list)
    execution_mode: str
    suggestions: str
    retry_count: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class QADecision(BaseModel):
    task_id: str
    approved: bool
    defect_report: DefectReport | None = None
    escalated: bool = False
    escalation_result: EscalationResult | None = None


class FrontendPhaseResult(BaseModel):
    project_id: str
    completed_tasks: list[str]
    total_tasks: int
    qa_cycles: int
    components_registered: list[str]
    agents_used: list[str]
    phase_duration_seconds: float


class TaskSummary(BaseModel):
    task_id: str
    title: str
    agent_id: str
    qa_cycles: int
    final_status: str


class PhaseCompletionReport(BaseModel):
    project_id: str
    phase: str
    completed_tasks: list[TaskSummary]
    total_tasks: int
    total_qa_cycles: int
    components_registry: list[ComponentEntry]
    navigation_contract_summary: str
    deferred_items: list[str] = Field(default_factory=list)
    compiled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    compiled_by: str = "lead_agent"


class PhaseGateResult(BaseModel):
    approved: bool
    approved_at: datetime | None = None
    feedback: str = ""
    api_contract_updated: bool = False


class APIContractReview(BaseModel):
    project_id: str
    original_contract: dict
    updated_contract: dict
    changes_made: list[str] = Field(default_factory=list)
    requires_update: bool
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
