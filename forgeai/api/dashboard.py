"""Observability dashboard HTML (Phase 10B)."""

from __future__ import annotations

import html
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.models.agent_lifecycle import AgentLifecycleEventModel
from forgeai.models.escalation import EscalationEventModel
from forgeai.models.project import ProjectModel
from forgeai.models.task import Task
from forgeai.state_machine.states import TaskState


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _short_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))[:8]
    except ValueError:
        return str(value)[:8]


def _ago(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    now = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _state_bar(counts: dict[str, int]) -> str:
    total = sum(counts.values()) or 1
    segments = [
        (TaskState.DONE.value, "#22c55e", "Done"),
        (TaskState.IN_PROGRESS.value, "#f59e0b", "In progress"),
        (TaskState.TESTING.value, "#eab308", "Testing"),
        (TaskState.PHASE_LOCKED.value, "#64748b", "Waiting"),
    ]
    parts = []
    for key, color, label in segments:
        n = counts.get(key, 0)
        if not n:
            continue
        pct = max(4, int(100 * n / total))
        parts.append(
            f'<span title="{_esc(label)}: {n}" style="background:{color};width:{pct}%;'
            f'display:inline-block;height:12px;"></span>'
        )
    if not parts:
        parts.append('<span style="background:#334155;width:100%;display:inline-block;height:12px;"></span>')
    return f'<div style="display:flex;width:100%;border-radius:4px;overflow:hidden;">{"".join(parts)}</div>'


async def build_dashboard_html(db: AsyncSession) -> str:
    refreshed = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    projects = list((await db.execute(select(ProjectModel).order_by(ProjectModel.created_at.desc()))).scalars())

    task_rows = await db.execute(
        select(Task.project_id, Task.current_state, func.count())
        .group_by(Task.project_id, Task.current_state)
    )
    by_project: dict[str, dict[str, int]] = {}
    for pid, state, n in task_rows.all():
        by_project.setdefault(str(pid), {})[state.value] = int(n)

    esc_rows = (
        await db.execute(
            select(EscalationEventModel)
            .order_by(EscalationEventModel.attempted_at.desc())
            .limit(50)
        )
    ).scalars().all()

    life_rows = (
        await db.execute(
            select(AgentLifecycleEventModel)
            .order_by(AgentLifecycleEventModel.timestamp.desc())
            .limit(20)
        )
    ).scalars().all()

    total_done = sum(c.get(TaskState.DONE.value, 0) for c in by_project.values())
    cost_est = round(total_done * 0.02, 2)

    project_rows = []
    for p in projects:
        pid = str(p.id)
        counts = by_project.get(pid, {})
        total = sum(counts.values())
        done = counts.get(TaskState.DONE.value, 0)
        status = {"ACTIVE": "In progress", "LIVE": "Delivered", "ARCHIVED": "Archived"}.get(
            p.status, p.status
        )
        project_rows.append(
            f"<tr>"
            f"<td class='mono'>{_esc(_short_id(pid))}</td>"
            f"<td>{_esc(p.name)}</td>"
            f"<td>{_esc(status)}</td>"
            f"<td>{done}/{total}</td>"
            f"<td>{_esc(_ago(p.created_at))}</td>"
            f"</tr>"
        )

    if not project_rows:
        project_rows.append(
            "<tr><td colspan='5' style='color:#94a3b8'>No projects yet</td></tr>"
        )

    summary_rows = []
    for p in projects[:12]:
        pid = str(p.id)
        counts = by_project.get(pid, {})
        summary_rows.append(
            f"<tr><td>{_esc(p.name)}</td><td>{_state_bar(counts)}</td></tr>"
        )
    if not summary_rows:
        summary_rows.append(
            "<tr><td colspan='2' style='color:#94a3b8'>No task data</td></tr>"
        )

    esc_table = []
    for e in esc_rows:
        outcome = "Needs you" if e.needs_human_input and not e.resolved else (
            "Resolved" if e.resolved else "Open"
        )
        esc_table.append(
            f"<tr>"
            f"<td class='mono'>{_esc(_short_id(str(e.task_id)))}</td>"
            f"<td>{e.level}</td>"
            f"<td>{_esc(outcome)}</td>"
            f"<td>{_esc(_ago(e.attempted_at))}</td>"
            f"</tr>"
        )
    if not esc_table:
        esc_table.append(
            "<tr><td colspan='4' style='color:#94a3b8'>No escalations recorded</td></tr>"
        )

    life_table = []
    for ev in life_rows:
        life_table.append(
            f"<tr>"
            f"<td class='mono'>{_esc(ev.agent_role)}</td>"
            f"<td>{_esc(ev.event_type)}</td>"
            f"<td>{_esc(ev.development_phase or '—')}</td>"
            f"<td>{_esc(_ago(ev.timestamp))}</td>"
            f"</tr>"
        )
    if not life_table:
        life_table.append(
            "<tr><td colspan='4' style='color:#94a3b8'>No agent events</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ForgeAI — Observability Dashboard</title>
  <meta http-equiv="refresh" content="5">
  <style>
    body {{ background:#0f172a; color:#e2e8f0; font-family:system-ui,sans-serif; margin:0; padding:24px; }}
    h1,h2 {{ color:#f8fafc; }}
    .card {{ background:#1e293b; border:1px solid #334155; border-radius:8px; padding:16px; margin-bottom:20px; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #334155; }}
    th {{ color:#94a3b8; font-weight:600; }}
    .mono {{ font-family:ui-monospace,monospace; font-size:13px; }}
    .muted {{ color:#94a3b8; font-size:13px; }}
    footer {{ margin-top:24px; color:#64748b; font-size:12px; }}
  </style>
</head>
<body>
  <h1>ForgeAI — Observability Dashboard</h1>
  <p class="muted">Last refreshed: {_esc(refreshed)} · Estimated spend: ${_esc(f"{cost_est:.2f}")}</p>

  <div class="card">
    <h2>Projects</h2>
    <table>
      <thead><tr><th>ID</th><th>Name</th><th>Status</th><th>Tasks</th><th>Age</th></tr></thead>
      <tbody>{"".join(project_rows)}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>Task state summary</h2>
    <table>
      <thead><tr><th>Project</th><th>Progress</th></tr></thead>
      <tbody>{"".join(summary_rows)}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>Recent escalations</h2>
    <table>
      <thead><tr><th>Task</th><th>Level</th><th>Outcome</th><th>When</th></tr></thead>
      <tbody>{"".join(esc_table)}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>Agent lifecycle events</h2>
    <table>
      <thead><tr><th>Role</th><th>Event</th><th>Phase</th><th>When</th></tr></thead>
      <tbody>{"".join(life_table)}</tbody>
    </table>
  </div>

  <footer>Auto-refreshes every 5 seconds · Internal use only</footer>
</body>
</html>"""
