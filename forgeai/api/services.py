"""API business logic (Phase 10B)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.api.schemas import (
    ApproveResponse,
    ChangeResponse,
    CreateProjectResponse,
    ProjectStatusResponse,
    ReportResponse,
)
from forgeai.config import get_settings
from forgeai.lifecycle.change_classifier import ChangeClassifier
from forgeai.lifecycle.impact_analyser import ImpactAnalyser
from forgeai.lifecycle.project_registry import ProjectRegistry
from forgeai.lifecycle.schemas import ProjectStatus
from forgeai.llm.client import LLMClient
from forgeai.llm.model_router import ModelRouter
from forgeai.llm.schemas import MasterDocument, ModelPool
from forgeai.models.escalation import EscalationEventModel
from forgeai.models.project import ProjectModel
from forgeai.models.project_artefact import ProjectArtefactModel
from forgeai.models.task import Task
from forgeai.state_machine.states import TaskState

logger = logging.getLogger(__name__)

_IN_PROGRESS_STATES = frozenset(
    {
        TaskState.IN_PROGRESS,
        TaskState.IN_REVIEW,
        TaskState.TESTING,
        TaskState.REWORK,
    }
)

_APPROVAL_LABELS = {
    "phase_gate": "Approve frontend before backend starts",
    "agent_count": "Approve agent team size",
    "tech_stack": "Approve technology stack selection",
    "delivery": "Approve final delivery",
}


def _snapshot(project: ProjectModel) -> dict[str, Any]:
    raw = project.project_memory_snapshot
    return raw if isinstance(raw, dict) else {}


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat()


def _human_status(status: str) -> str:
    labels = {
        "ACTIVE": "In progress",
        "LIVE": "Delivered",
        "ARCHIVED": "Archived",
    }
    return labels.get(status, status.replace("_", " ").title())


def _derive_phase(
    project_status: str,
    counts: dict[str, int],
    bootstrap_status: str | None,
) -> str:
    if project_status == "LIVE":
        return "Delivered"
    if project_status == "ARCHIVED":
        return "Archived"
    if bootstrap_status == "running":
        return "Planning"
    if bootstrap_status == "failed":
        return "Planning (needs attention)"
    total = sum(counts.values())
    if total == 0:
        return "Planning"
    done = counts.get(TaskState.DONE.value, 0)
    locked = counts.get(TaskState.PHASE_LOCKED.value, 0)
    in_prog = sum(counts.get(s.value, 0) for s in _IN_PROGRESS_STATES)
    testing = counts.get(TaskState.TESTING.value, 0)
    if done == total:
        return "Final review"
    if testing > 0 or in_prog > 0:
        if counts.get(TaskState.TODO.value, 0) > 0 and locked > 0:
            return "Building features"
        return "Quality review"
    if locked > done:
        return "Waiting on dependencies"
    if done > 0:
        return "Building features"
    return "Planning"


def _status_message(
    name: str,
    phase: str,
    done: int,
    total: int,
    in_progress: int,
    pending: list[str],
) -> str:
    parts = [f"{name} is {phase.lower()}."]
    if total:
        parts.append(f"{done} of {total} work items are complete.")
    if in_progress:
        parts.append(f"{in_progress} still in progress.")
    if pending:
        parts.append(f"Waiting on: {pending[0]}.")
    return " ".join(parts)


async def _task_counts(session: AsyncSession, project_id: uuid.UUID) -> dict[str, int]:
    rows = await session.execute(
        select(Task.current_state, func.count())
        .where(Task.project_id == project_id)
        .group_by(Task.current_state)
    )
    return {state.value: int(n) for state, n in rows.all()}


async def create_project_record(
    session: AsyncSession,
    brief: str,
    constraints: dict,
    name: str,
) -> CreateProjectResponse:
    registry = ProjectRegistry(session)
    display = name.strip() or "New project"
    project = await registry.create_project(display, brief)
    row = await session.get(ProjectModel, uuid.UUID(project.id))
    if row:
        row.project_memory_snapshot = {
            "brief": brief,
            "constraints": constraints,
            "bootstrap_status": "queued",
            "pending_approvals": [
                "Approve technology stack selection",
            ],
        }
        await session.commit()

    pid = project.id
    return CreateProjectResponse(
        project_id=pid,
        status="bootstrapping",
        message=(
            f"We started planning {display}. "
            "Research and architecture run in the background; check back shortly."
        ),
        poll_url=f"/projects/{pid}",
    )


async def get_project_status(
    session: AsyncSession,
    project_id: str,
) -> ProjectStatusResponse:
    try:
        pid = uuid.UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc

    row = await session.get(ProjectModel, pid)
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")

    snap = _snapshot(row)
    counts = await _task_counts(session, pid)
    total = sum(counts.values())
    done = counts.get(TaskState.DONE.value, 0)
    in_progress = sum(counts.get(s.value, 0) for s in _IN_PROGRESS_STATES)
    pending = list(snap.get("pending_approvals") or [])
    if not pending and row.status == ProjectStatus.ACTIVE.value:
        pending = ["Approve technology stack selection"]

    esc = await session.execute(
        select(func.count())
        .select_from(EscalationEventModel)
        .where(
            EscalationEventModel.task_id.in_(
                select(Task.id).where(Task.project_id == pid)
            ),
            EscalationEventModel.needs_human_input.is_(True),
            EscalationEventModel.resolved.is_(False),
        )
    )
    escalations = int(esc.scalar_one() or 0)

    phase = _derive_phase(row.status, counts, snap.get("bootstrap_status"))
    cost = round(done * 0.02 + in_progress * 0.005, 2)

    return ProjectStatusResponse(
        project_id=str(pid),
        name=row.name,
        status=_human_status(row.status),
        phase=phase,
        message=_status_message(row.name, phase, done, total, in_progress, pending),
        tasks_done=done,
        tasks_total=total,
        tasks_in_progress=in_progress,
        cost_usd=cost,
        pending_approvals=pending,
        escalations_needing_input=escalations,
        created_at=_iso(row.created_at) or "",
        delivered_at=_iso(row.delivered_at),
    )


async def approve_project(
    session: AsyncSession,
    project_id: str,
    approval_type: str,
    notes: str,
) -> ApproveResponse:
    try:
        pid = uuid.UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc

    row = await session.get(ProjectModel, pid)
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")

    label = _APPROVAL_LABELS.get(approval_type, approval_type.replace("_", " "))
    snap = _snapshot(row)
    pending = list(snap.get("pending_approvals") or [])
    approved_items = list(snap.get("approved_items") or [])

    matched = False
    new_pending = []
    for item in pending:
        if label.lower() in item.lower() or approval_type in item.lower():
            matched = True
            approved_items.append({"type": approval_type, "notes": notes, "at": _iso(datetime.now(UTC))})
        else:
            new_pending.append(item)

    if not matched and pending:
        new_pending = pending[1:]
        approved_items.append({"type": approval_type, "notes": notes, "at": _iso(datetime.now(UTC))})
    elif not matched:
        approved_items.append({"type": approval_type, "notes": notes, "at": _iso(datetime.now(UTC))})

    snap["pending_approvals"] = new_pending
    snap["approved_items"] = approved_items
    row.project_memory_snapshot = snap
    await session.commit()

    if approval_type == "phase_gate":
        next_msg = "Frontend work can continue; backend tasks will unlock when the gate clears."
    elif approval_type == "tech_stack":
        next_msg = "Technology choices are recorded; agents will follow the approved stack."
    elif approval_type == "agent_count":
        next_msg = "Team size is approved; task assignment proceeds with this configuration."
    elif approval_type == "delivery":
        next_msg = "Delivery is approved; the project can move to live status."
    else:
        next_msg = "Your approval was recorded; the team will continue with the next step."

    return ApproveResponse(
        project_id=project_id,
        approved=True,
        message=next_msg,
    )


async def _load_master_document(session: AsyncSession, pid: uuid.UUID) -> MasterDocument | None:
    res = await session.execute(
        select(ProjectArtefactModel).where(
            ProjectArtefactModel.project_id == pid,
            ProjectArtefactModel.artefact_type == "master_document",
            ProjectArtefactModel.is_current.is_(True),
        )
    )
    art = res.scalar_one_or_none()
    if art and isinstance(art.content, dict):
        try:
            return MasterDocument.model_validate(art.content)
        except Exception:
            logger.warning("Could not parse master_document for %s", pid)
    return None


def _heuristic_change(change_request: str) -> tuple[str, str, str]:
    lower = change_request.lower()
    if any(w in lower for w in ("bug", "fix", "broken", "error", "crash")):
        return "Bug fix", "Low", "SMALL_FEATURE"
    if any(w in lower for w in ("refactor", "architecture", "database schema", "migrate")):
        return "Structural change", "High", "ARCHITECTURAL"
    if any(w in lower for w in ("add", "new feature", "collaboration", "team")):
        return "Large feature", "High", "LARGE_FEATURE"
    return "Small feature", "Medium", "SMALL_FEATURE"


async def analyse_change(
    session: AsyncSession,
    project_id: str,
    change_request: str,
    decision: str,
) -> ChangeResponse:
    try:
        pid = uuid.UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc

    registry = ProjectRegistry(session)
    project = await registry.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    master = await _load_master_document(session, pid)
    settings = get_settings()
    change_type_label, risk_label, change_type_raw = _heuristic_change(change_request)
    affected = 0
    cost = 0.15
    minutes = 15
    summary = (
        f"Recorded as a {change_type_label.lower()} with {risk_label.lower()} risk. "
        f"Decision noted: {decision}. No work was started — submit PROCEED again after review to execute."
    )

    if settings.anthropic_api_key.strip() and master is not None:
        try:
            from forgeai.lifecycle.schemas import ChangeClassification, ChangeType, RiskLevel

            pool = ModelPool.from_env()
            router = ModelRouter(pool)
            llm = LLMClient(settings.anthropic_api_key, router)
            classifier = ChangeClassifier(llm)
            status = ProjectStatus(project.status)
            classification = await classifier.classify(change_request, master, status)
            analyser = ImpactAnalyser(llm, session)
            impact = await analyser.analyse(
                change_request,
                classification,
                project_id,
                master,
            )
            change_type_label = classification.change_type.value.replace("_", " ").title()
            risk_label = classification.risk_level.value.title()
            change_type_raw = classification.change_type.value
            affected = len(impact.affected_task_ids) + len(impact.new_tasks_required)
            cost = impact.estimated_cost_usd
            minutes = impact.estimated_time_minutes
            summary = impact.human_message or summary
        except Exception:
            logger.exception("Change analysis failed for %s; using heuristic", project_id)

    counts = await _task_counts(session, pid)
    if affected == 0:
        affected = max(1, counts.get(TaskState.DONE.value, 0))

    proj_row = await session.get(ProjectModel, pid)
    if proj_row is not None:
        snap = _snapshot(proj_row)
        snap["last_change_analysis"] = {
            "change_request": change_request,
            "decision": decision,
            "change_type": change_type_raw,
        }
        proj_row.project_memory_snapshot = snap
        await session.commit()

    return ChangeResponse(
        project_id=project_id,
        change_type=change_type_label,
        risk_level=risk_label,
        affected_tasks=affected,
        estimated_cost_usd=round(cost, 2),
        estimated_time_minutes=minutes,
        decision=decision,
        message=summary,
    )


async def get_project_report(
    session: AsyncSession,
    project_id: str,
) -> ReportResponse:
    try:
        pid = uuid.UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc

    row = await session.get(ProjectModel, pid)
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")

    snap = _snapshot(row)
    counts = await _task_counts(session, pid)
    done = counts.get(TaskState.DONE.value, 0)

    res = await session.execute(
        select(ProjectArtefactModel).where(
            ProjectArtefactModel.project_id == pid,
            ProjectArtefactModel.artefact_type == "final_summary",
            ProjectArtefactModel.is_current.is_(True),
        )
    )
    art = res.scalar_one_or_none()
    gaps: list[str] = []
    files_written: list[str] = []
    qa_cycles = 0
    escalations = 0
    lessons = 0
    cost = round(done * 0.02, 2)
    generated_at = _iso(datetime.now(UTC)) or ""

    if art and isinstance(art.content, dict):
        content = art.content
        qa_cycles = int(content.get("total_qa_cycles") or content.get("qa_cycles") or 0)
        escalations = int(content.get("escalations_total") or content.get("escalations") or 0)
        lessons = int(content.get("lessons_accumulated") or 0)
        cost = float(content.get("total_cost_usd") or cost)
        generated_at = content.get("generated_at") or generated_at
        gaps = list(content.get("gaps_identified") or [])

    esc_count = await session.execute(
        select(func.count()).select_from(EscalationEventModel).where(
            EscalationEventModel.task_id.in_(
                select(Task.id).where(Task.project_id == pid)
            )
        )
    )
    if not escalations:
        escalations = int(esc_count.scalar_one() or 0)

    output_dir = None
    if row.status == ProjectStatus.LIVE.value:
        output_dir = str(Path("H:/forgeai-output") / str(pid))
        root = Path(output_dir)
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file() and ".git" not in path.parts:
                    files_written.append(str(path.relative_to(root)).replace("\\", "/"))

    if not gaps and done < counts.get(TaskState.TODO.value, 0) + done:
        gaps.append("Some planned work items were not completed.")

    return ReportResponse(
        project_id=project_id,
        name=row.name,
        brief=row.brief or str(snap.get("brief") or ""),
        release_tag=row.release_tag,
        tasks_completed=done,
        qa_cycles=qa_cycles,
        escalations=escalations,
        lessons_accumulated=lessons,
        cost_usd=cost,
        output_directory=output_dir,
        files_written=files_written[:50],
        gaps_identified=gaps,
        generated_at=generated_at,
    )
