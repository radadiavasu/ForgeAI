"""Lifecycle and change-management schemas (Phase 9B)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from forgeai.bootstrap.schemas import TaskSpec


class ProjectStatus(str, Enum):
    ACTIVE = "ACTIVE"
    LIVE = "LIVE"
    ARCHIVED = "ARCHIVED"


class Project(BaseModel):
    id: str
    name: str
    brief: str
    status: ProjectStatus
    created_at: datetime
    delivered_at: datetime | None = None
    archived_at: datetime | None = None
    release_tag: str | None = None


class ChangeType(str, Enum):
    BUGFIX = "BUGFIX"
    SMALL_FEATURE = "SMALL_FEATURE"
    LARGE_FEATURE = "LARGE_FEATURE"
    ARCHITECTURAL = "ARCHITECTURAL"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    ARCHITECTURAL = "ARCHITECTURAL"


class ChangeClassification(BaseModel):
    change_type: ChangeType
    risk_level: RiskLevel
    reasoning: str
    requires_human_confirmation: bool
    estimated_new_tasks: int = 0
    classified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ImpactAnalysis(BaseModel):
    project_id: str
    change_request: str
    classification: ChangeClassification
    affected_task_ids: list[str] = Field(default_factory=list)
    affected_task_titles: list[str] = Field(default_factory=list)
    conflicting_task_ids: list[str] = Field(default_factory=list)
    new_tasks_required: list[str] = Field(default_factory=list)
    estimated_cost_usd: float = 0.0
    estimated_time_minutes: int = 0
    human_message: str = ""
    analysed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChangeDecision(str, Enum):
    PROCEED = "PROCEED"
    QUEUE = "QUEUE"
    DEFER = "DEFER"
    REJECT = "REJECT"


class HumanChangeApproval(BaseModel):
    project_id: str
    change_request: str
    impact_analysis: ImpactAnalysis
    decision: ChangeDecision
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_by: str = "human"


class ChangeSpecDocument(BaseModel):
    project_id: str
    change_request: str
    summary: str
    new_components: list[str] = Field(default_factory=list)
    modified_components: list[str] = Field(default_factory=list)
    new_api_surfaces: list[str] = Field(default_factory=list)
    modified_api_surfaces: list[str] = Field(default_factory=list)
    new_tasks: list[TaskSpec] = Field(default_factory=list)
    rework_tasks: list[str] = Field(default_factory=list)
    estimated_cost_usd: float = 0.0
    estimated_time_minutes: int = 0
    version: str = "1.0"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PatchResult(BaseModel):
    project_id: str
    change_request: str
    rework_tasks_completed: list[str] = Field(default_factory=list)
    new_tasks_completed: list[str] = Field(default_factory=list)
    regression_tests_passed: bool = True
    regression_failures: list[str] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RegressionResult(BaseModel):
    tasks_checked: list[str] = Field(default_factory=list)
    all_passed: bool = True
    failures: list[str] = Field(default_factory=list)


class ChangeResult(BaseModel):
    project_id: str
    change_request: str
    change_spec: ChangeSpecDocument | None = None
    new_tasks_completed: list[str] = Field(default_factory=list)
    rework_tasks_completed: list[str] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChangeHistoryEntry(BaseModel):
    entry_id: str
    project_id: str
    change_request: str
    classification: ChangeClassification
    impact_analysis: ImpactAnalysis
    human_decision: HumanChangeApproval
    execution_result: PatchResult | ChangeResult | None = None
    outcome: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
