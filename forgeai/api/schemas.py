"""API request and response models (Phase 10B)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    brief: str
    constraints: dict = Field(default_factory=dict)
    name: str = ""


class CreateProjectResponse(BaseModel):
    project_id: str
    status: str
    message: str
    poll_url: str


class ProjectStatusResponse(BaseModel):
    project_id: str
    name: str
    status: str
    phase: str
    message: str
    tasks_done: int
    tasks_total: int
    tasks_in_progress: int
    cost_usd: float
    pending_approvals: list[str]
    escalations_needing_input: int
    created_at: str
    delivered_at: str | None


class ApproveRequest(BaseModel):
    approval_type: str
    notes: str = ""


class ApproveResponse(BaseModel):
    project_id: str
    approved: bool
    message: str


class ChangeRequest(BaseModel):
    change_request: str
    decision: str = "PROCEED"


class ChangeResponse(BaseModel):
    project_id: str
    change_type: str
    risk_level: str
    affected_tasks: int
    estimated_cost_usd: float
    estimated_time_minutes: int
    decision: str
    message: str


class ReportResponse(BaseModel):
    project_id: str
    name: str
    brief: str
    release_tag: str | None
    tasks_completed: int
    qa_cycles: int
    escalations: int
    lessons_accumulated: int
    cost_usd: float
    output_directory: str | None
    files_written: list[str]
    gaps_identified: list[str]
    generated_at: str
