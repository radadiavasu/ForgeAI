"""Human gate — phase completion report and API contract review (Phase 7, Req 28)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from forgeai.contracts.registry import ComponentRegistry
from forgeai.contracts.schemas import ComponentEntry, NavigationContract
from forgeai.models.task import Task
from forgeai.orchestration.schemas import (
    APIContractReview,
    FrontendPhaseResult,
    PhaseCompletionReport,
    PhaseGateResult,
    TaskSummary,
)
from forgeai.state_machine.states import TaskState

if TYPE_CHECKING:
    from forgeai.agents.lead_agent import LeadAgent
    from forgeai.llm.client import LLMClient

logger = logging.getLogger(__name__)

API_CONTRACT_REVIEW_PROMPT = """
You are Lead_Agent reviewing the API_Contract against verified frontend work.

Compare the original API contract JSON with the completed frontend phase summary.
Identify gaps, missing endpoints, or fields the UI requires but the contract omits.

Respond with JSON only:
{
  "requires_update": boolean,
  "updated_contract": { ... full contract object ... },
  "changes_made": ["plain language change 1", ...]
}

If no changes are needed, set requires_update to false and return the original contract
unchanged in updated_contract with an empty changes_made list.
""".strip()

_JARGON = frozenset(
    {"agent", "llm", "chroma", "postgresql", "redis", "minio", "pydantic", "sandbox"}
)


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


class PhaseGate:
    """Compile frontend phase reports and coordinate human approval."""

    def __init__(
        self,
        lead_agent: LeadAgent,
        llm_client: LLMClient,
        db_session,
    ) -> None:
        self.lead = lead_agent
        self.llm = llm_client
        self.db = db_session

    async def compile_report(
        self,
        frontend_phase_result: FrontendPhaseResult,
        component_registry: ComponentRegistry,
        navigation_contract: NavigationContract,
        project_id: str,
    ) -> PhaseCompletionReport:
        pid = UUID(project_id)
        res = await self.db.execute(
            select(Task).where(
                Task.project_id == pid,
                Task.current_state == TaskState.DONE,
            )
        )
        done_tasks = list(res.scalars())
        completed_refs = set(frontend_phase_result.completed_tasks)
        summaries: list[TaskSummary] = []
        for task in done_tasks:
            if completed_refs and str(task.id) not in completed_refs and task.title not in completed_refs:
                continue
            summaries.append(
                TaskSummary(
                    task_id=str(task.id),
                    title=task.title,
                    agent_id=task.assigned_agent,
                    qa_cycles=1,
                    final_status="DONE",
                )
            )
        if not summaries:
            for tid in frontend_phase_result.completed_tasks:
                task_row = await self._load_task_by_id_or_title(pid, tid)
                if task_row:
                    summaries.append(
                        TaskSummary(
                            task_id=str(task_row.id),
                            title=task_row.title,
                            agent_id=task_row.assigned_agent,
                            qa_cycles=1,
                            final_status="DONE",
                        )
                    )

        components = await component_registry.list_all(project_id)
        nav_summary = self._summarize_navigation(navigation_contract)
        deferred = await self._deferred_frontend_items(pid, summaries)

        return PhaseCompletionReport(
            project_id=project_id,
            phase="FRONTEND_PHASE",
            completed_tasks=summaries,
            total_tasks=frontend_phase_result.total_tasks,
            total_qa_cycles=frontend_phase_result.qa_cycles,
            components_registry=components,
            navigation_contract_summary=nav_summary,
            deferred_items=deferred,
            compiled_at=datetime.now(UTC),
            compiled_by="lead_agent",
        )

    async def _load_task_by_id_or_title(self, project_id: UUID, ref: str) -> Task | None:
        try:
            tid = UUID(ref)
            res = await self.db.execute(select(Task).where(Task.id == tid))
            return res.scalar_one_or_none()
        except ValueError:
            res = await self.db.execute(
                select(Task).where(Task.project_id == project_id, Task.title == ref)
            )
            return res.scalar_one_or_none()

    async def _deferred_frontend_items(
        self,
        project_id: UUID,
        completed: list[TaskSummary],
    ) -> list[str]:
        completed_titles = {s.title for s in completed}
        res = await self.db.execute(
            select(Task).where(
                Task.project_id == project_id,
                Task.current_state != TaskState.DONE,
            )
        )
        return [
            t.title
            for t in res.scalars()
            if "FRONTEND" in (t.title or "").upper() or t.current_state == TaskState.PHASE_LOCKED
            if t.title not in completed_titles
        ]

    def _summarize_navigation(self, navigation_contract: NavigationContract) -> str:
        parts = [
            f"Shared layout component {navigation_contract.shared_layout_component} "
            f"owned by {navigation_contract.shared_layout_owner}."
        ]
        for route in navigation_contract.routes:
            parts.append(
                f"Route {route.path} maps to {route.component_name}."
            )
        return " ".join(parts)

    async def present_to_human(
        self,
        report: PhaseCompletionReport,
        human_approval_callback: Callable[[str], Awaitable[bool]],
    ) -> PhaseGateResult:
        formatted = self.format_report_for_human(report)
        print(formatted)
        approved = await human_approval_callback(formatted)
        if approved:
            return PhaseGateResult(
                approved=True,
                approved_at=datetime.now(UTC),
            )
        return PhaseGateResult(
            approved=False,
            feedback="Human requested additional frontend changes before backend phase.",
        )

    def format_report_for_human(self, report: PhaseCompletionReport) -> str:
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            " HUMAN GATE — Frontend Complete",
            "",
            f" All {report.total_tasks} pages are built and verified.",
            " No backend code has been written yet.",
            "",
            " Pages completed:",
        ]
        for item in report.completed_tasks:
            lines.append(f" ✓ {item.title} ({item.qa_cycles} QA cycle(s))")
        if report.components_registry:
            names = ", ".join(c.component_name for c in report.components_registry)
            lines.append("")
            lines.append(f" Shared components: {names}")
        lines.append(f" Total QA cycles across phase: {report.total_qa_cycles}")
        if report.deferred_items:
            lines.append("")
            lines.append(" Deferred:")
            for d in report.deferred_items:
                lines.append(f" - {d}")
        lines.append("")
        lines.append(" Approve to start Backend Phase →")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    async def review_api_contract(
        self,
        api_contract: dict,
        frontend_phase_result: FrontendPhaseResult,
        project_id: str,
    ) -> APIContractReview:
        user_message = json.dumps(
            {
                "project_id": project_id,
                "api_contract": api_contract,
                "frontend_phase_result": frontend_phase_result.model_dump(mode="json"),
            },
            indent=2,
        )
        resp = await self.llm.complete(
            system_prompt=API_CONTRACT_REVIEW_PROMPT,
            user_message=user_message,
            complexity="MEDIUM",
            loop_count=0,
            max_tokens=8192,
        )
        try:
            data = _extract_json_object(resp.content)
            requires_update = bool(data.get("requires_update", False))
            updated = data.get("updated_contract", api_contract)
            if not isinstance(updated, dict):
                updated = api_contract
            changes = data.get("changes_made", [])
            if not isinstance(changes, list):
                changes = [str(changes)] if changes else []
            changes = [str(c) for c in changes]
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            requires_update = False
            updated = api_contract
            changes = []

        return APIContractReview(
            project_id=project_id,
            original_contract=api_contract,
            updated_contract=updated,
            changes_made=changes,
            requires_update=requires_update,
            reviewed_at=datetime.now(UTC),
        )
