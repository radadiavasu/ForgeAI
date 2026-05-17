"""Deployment and version-control schemas (Phase 10)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class GitCommit(BaseModel):
    hash: str
    message: str
    author: str
    timestamp: datetime
    task_id: str | None = None
    agent_id: str | None = None


class RollbackPoint(BaseModel):
    tag_name: str
    message: str
    created_at: datetime
    commit_hash: str


class DeploymentPackage(BaseModel):
    project_id: str
    output_dir: str
    files_written: list[str] = Field(default_factory=list)
    dockerfile_path: str = ""
    docker_compose_path: str = ""
    env_example_path: str = ""
    readme_path: str = ""
    summary_report_path: str = ""
    release_tag: str = "release-v1"
    git_log: list[GitCommit] = Field(default_factory=list)
    rollback_points: list[RollbackPoint] = Field(default_factory=list)
    docker_build_passed: bool = False
    assembled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_size_bytes: int = 0


class FinalSummaryReport(BaseModel):
    project_id: str
    project_name: str
    project_brief: str
    total_tasks_completed: int
    total_qa_cycles: int
    total_cost_usd: float
    total_duration_minutes: float
    tasks_by_phase: dict[str, int] = Field(default_factory=dict)
    escalations_total: int = 0
    escalations_resolved_automatically: int = 0
    escalations_requiring_human: int = 0
    lessons_accumulated: int = 0
    rollback_points: list[str] = Field(default_factory=list)
    release_tag: str = "release-v1"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def format_final_summary_plain(report: FinalSummaryReport) -> str:
    """Plain-language SUMMARY.md body."""
    delivered = report.generated_at.strftime("%Y-%m-%d")
    lines = [
        "ForgeAI Project Summary",
        "=======================",
        f"Project: {report.project_name}",
        f"Delivered: {delivered}",
        "",
        "What was built:",
        report.project_brief,
        "",
        "What was completed:",
        f"  {report.tasks_by_phase.get('FRONTEND_PHASE', 0)} pages and components built",
        f"  {report.tasks_by_phase.get('BACKEND_PHASE', 0)} API endpoints implemented",
        "  All code tested and verified",
        "",
        "How it went:",
        f"  {report.total_tasks_completed} tasks completed",
        f"  {report.escalations_resolved_automatically} quality issues caught and fixed automatically",
        f"  {report.escalations_requiring_human} issues required your input",
        f"  Total time: ~{int(report.total_duration_minutes)} minutes",
        f"  Estimated API cost: ~${report.total_cost_usd:.2f}",
        "",
        "Knowledge gained:",
        f"  {report.lessons_accumulated} lessons written for future projects",
        "",
        "Delivery:",
        f"  Git tag: {report.release_tag}",
        "  To run: docker compose up",
    ]
    return "\n".join(lines)
