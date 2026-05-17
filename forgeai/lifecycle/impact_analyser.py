"""Impact analysis before change execution (Phase 9B)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from forgeai.lifecycle.schemas import ChangeClassification, ChangeType, ImpactAnalysis
from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import MasterDocument
from forgeai.models.task import Task
from forgeai.state_machine.states import TaskState

IMPACT_ANALYSER_PROMPT = """
You are Lead_Agent analysing the impact of a classified change request.

Given DONE and IN_PROGRESS tasks, determine affected work.

Return JSON only:
{
  "affected_task_titles": ["title1", ...],
  "conflicting_task_titles": ["title in progress", ...],
  "new_tasks_required": ["new task title", ...],
  "estimated_cost_usd": <float>,
  "estimated_time_minutes": <integer>
}
""".strip()

_JARGON = frozenset({"agent", "llm", "chroma", "postgresql", "artefact", "artefact"})


def _strip_json_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s.strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    s = _strip_json_fence(text)
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else {"items": out}
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            out = json.loads(m.group(0))
            return out if isinstance(out, dict) else {"items": out}
        raise


def _normalize_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _plain_type_label(classification: ChangeClassification) -> str:
    labels = {
        "BUGFIX": "Bug fix",
        "SMALL_FEATURE": "Small feature",
        "LARGE_FEATURE": "Large feature",
        "ARCHITECTURAL": "Structural change",
    }
    return labels.get(classification.change_type.value, classification.change_type.value)


def _plain_risk_label(classification: ChangeClassification) -> str:
    labels = {
        "LOW": "Low",
        "MEDIUM": "Medium",
        "HIGH": "High",
        "ARCHITECTURAL": "Structural — requires careful review",
    }
    return labels.get(classification.risk_level.value, classification.risk_level.value)


def _calibrated_estimates(
    classification: ChangeClassification,
    affected_count: int,
    new_count: int,
) -> tuple[float, int | str]:
    """Cost (USD) and time (minutes or assessment message) from Phase 8–9B calibration."""
    ct = classification.change_type
    if ct == ChangeType.ARCHITECTURAL:
        return 0.0, "Requires assessment"
    if ct == ChangeType.BUGFIX:
        return affected_count * 0.05, affected_count * 5
    if ct == ChangeType.SMALL_FEATURE:
        total = affected_count + new_count
        return total * 0.08, total * 8
    if ct == ChangeType.LARGE_FEATURE:
        total = affected_count + new_count
        return 0.25 + total * 0.10, 30 + total * 12
    total = affected_count + new_count
    return total * 0.08, total * 8


def format_human_message(
    classification: ChangeClassification,
    affected_count: int,
    conflicting_count: int,
    new_count: int,
    cost: float,
    minutes: int | str,
) -> str:
    lines = [
        "This change request affects your project as follows:",
        "",
        f"Change type: {_plain_type_label(classification)}",
        f"Risk level: {_plain_risk_label(classification)}",
        "",
        "Work affected:",
        f"  {affected_count} completed tasks will need to be revisited",
        f"  {conflicting_count} tasks currently in progress will be interrupted",
        f"  {new_count} new tasks will be created",
        "",
    ]
    if classification.change_type == ChangeType.ARCHITECTURAL:
        lines.extend(
            [
                "Estimated additional cost: Requires assessment — contact your team",
                "Estimated additional time: Requires assessment",
            ]
        )
    else:
        lines.extend(
            [
                f"Estimated additional cost: ~${cost:.2f}",
                f"Estimated additional time: ~{minutes} minutes",
            ]
        )
    lines.extend(
        [
            "",
            "What would you like to do?",
            "  PROCEED — start immediately",
            "  QUEUE   — complete current tasks first, then start",
            "  DEFER   — implement when current phase completes",
            "  REJECT  — do not implement this change",
        ]
    )
    return "\n".join(lines)


class ImpactAnalyser:
    """Analyse scope, cost, and risk before executing a change."""

    def __init__(self, llm_client: LLMClient, db_session) -> None:
        self.llm = llm_client
        self.db = db_session

    async def analyse(
        self,
        change_request: str,
        classification: ChangeClassification,
        project_id: str,
        master_document: MasterDocument,
    ) -> ImpactAnalysis:
        pid = UUID(project_id)
        res = await self.db.execute(select(Task).where(Task.project_id == pid))
        tasks = list(res.scalars())
        done_tasks = [t for t in tasks if t.current_state == TaskState.DONE]
        in_progress = [
            t
            for t in tasks
            if t.current_state in (TaskState.IN_PROGRESS, TaskState.IN_REVIEW, TaskState.TESTING)
        ]
        task_context = [
            {
                "id": str(t.id),
                "title": t.title,
                "state": t.current_state.value,
            }
            for t in tasks
        ]
        user_message = json.dumps(
            {
                "change_request": change_request,
                "classification": classification.model_dump(mode="json"),
                "tasks": task_context,
                "master_document": master_document.model_dump(mode="json"),
            },
            indent=2,
        )[:50000]

        async def _parse(complexity: str) -> dict[str, Any]:
            resp = await self.llm.complete(
                system_prompt=IMPACT_ANALYSER_PROMPT,
                user_message=user_message,
                complexity=complexity,
                loop_count=0,
                max_tokens=4096,
            )
            data = _extract_json_object(resp.content)
            return {
                "affected_task_titles": _normalize_string_list(data.get("affected_task_titles")),
                "conflicting_task_titles": _normalize_string_list(
                    data.get("conflicting_task_titles")
                ),
                "new_tasks_required": _normalize_string_list(data.get("new_tasks_required")),
                "estimated_cost_usd": float(data.get("estimated_cost_usd", 0.02)),
                "estimated_time_minutes": int(data.get("estimated_time_minutes", 5)),
            }

        try:
            raw = await _parse("MEDIUM")
        except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            raw = {
                "affected_task_titles": [done_tasks[0].title] if done_tasks else [],
                "conflicting_task_titles": [t.title for t in in_progress],
                "new_tasks_required": [],
                "estimated_cost_usd": 0.02,
                "estimated_time_minutes": 5,
            }

        title_to_id = {t.title: str(t.id) for t in tasks}
        affected_titles = raw["affected_task_titles"]
        if not affected_titles and done_tasks:
            affected_titles = [done_tasks[0].title]
        affected_ids = [title_to_id[t] for t in affected_titles if t in title_to_id]
        conflicting_ids = [
            title_to_id[t] for t in raw["conflicting_task_titles"] if t in title_to_id
        ]

        cost_usd, time_est = _calibrated_estimates(
            classification,
            len(affected_ids),
            len(raw["new_tasks_required"]),
        )
        time_minutes = 0 if isinstance(time_est, str) else int(time_est)

        human_message = format_human_message(
            classification,
            len(affected_ids),
            len(conflicting_ids),
            len(raw["new_tasks_required"]),
            cost_usd,
            time_est,
        )

        return ImpactAnalysis(
            project_id=project_id,
            change_request=change_request,
            classification=classification,
            affected_task_ids=affected_ids,
            affected_task_titles=affected_titles,
            conflicting_task_ids=conflicting_ids,
            new_tasks_required=raw["new_tasks_required"],
            estimated_cost_usd=cost_usd,
            estimated_time_minutes=time_minutes,
            human_message=human_message,
            analysed_at=datetime.now(UTC),
        )
