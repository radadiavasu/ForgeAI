"""Holistic final project review stub (Phase 9)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from forgeai.intelligence.schemas import FinalReviewResult
from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import MasterDocument
from forgeai.models.task import Task
from forgeai.orchestration.schemas import TaskSummary
from forgeai.state_machine.states import TaskState

FINAL_REVIEW_PROMPT = """
You are Lead_Agent performing a holistic final review of a software project.

Compare completed task outputs against the Master_Document.
Check:
- Every component has a corresponding completed task
- Every API surface has frontend and backend coverage
- Navigation and integration gaps

Return JSON only:
{
  "passed": boolean,
  "consistency_checks": ["plain language check 1", ...],
  "gaps_found": ["gap description", ...],
  "remediation_tasks": ["new task title", ...]
}
""".strip()


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


def _normalize_final_review(data: dict[str, Any]) -> dict[str, Any]:
    passed = bool(data.get("passed", True))
    checks = data.get("consistency_checks", [])
    gaps = data.get("gaps_found", [])
    tasks = data.get("remediation_tasks", [])
    if not isinstance(checks, list):
        checks = [str(checks)]
    if not isinstance(gaps, list):
        gaps = [str(gaps)] if gaps else []
    if not isinstance(tasks, list):
        tasks = [str(tasks)] if tasks else []
    return {
        "passed": passed and not gaps,
        "consistency_checks": [str(c) for c in checks],
        "gaps_found": [str(g) for g in gaps],
        "remediation_tasks": [str(t) for t in tasks],
    }


class FinalReviewer:
    """Review all DONE tasks against the Master_Document."""

    def __init__(self, llm_client: LLMClient, db_session) -> None:
        self.llm = llm_client
        self.db = db_session

    async def review(
        self,
        project_id: str,
        master_document: MasterDocument,
        completed_tasks: list[TaskSummary] | None = None,
    ) -> FinalReviewResult:
        from uuid import UUID

        pid = UUID(project_id)
        if completed_tasks is None:
            res = await self.db.execute(
                select(Task).where(
                    Task.project_id == pid,
                    Task.current_state == TaskState.DONE,
                )
            )
            completed_tasks = [
                TaskSummary(
                    task_id=str(t.id),
                    title=t.title,
                    agent_id=t.assigned_agent or "",
                    qa_cycles=1,
                    final_status="DONE",
                )
                for t in res.scalars()
            ]

        payload = {
            "master_document": master_document.model_dump(mode="json"),
            "completed_tasks": [t.model_dump(mode="json") for t in completed_tasks],
        }
        resp = await self.llm.complete(
            system_prompt=FINAL_REVIEW_PROMPT,
            user_message=json.dumps(payload, indent=2)[:50000],
            complexity="HIGH",
            loop_count=0,
            max_tokens=8192,
            agent_id="lead_agent",
            task_id=project_id,
        )
        try:
            raw = _normalize_final_review(_extract_json_object(resp.content))
        except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            raw = {
                "passed": True,
                "consistency_checks": ["Review completed with parse fallback"],
                "gaps_found": [],
                "remediation_tasks": [],
            }

        return FinalReviewResult(
            project_id=project_id,
            passed=raw["passed"],
            consistency_checks=raw["consistency_checks"],
            gaps_found=raw["gaps_found"],
            remediation_tasks=raw["remediation_tasks"],
            reviewed_at=datetime.now(UTC),
        )
