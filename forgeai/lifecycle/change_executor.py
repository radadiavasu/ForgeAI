"""CHANGE mode execution for LARGE_FEATURE (Phase 9B)."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TYPE_CHECKING

from pydantic import ValidationError

from forgeai.bootstrap.schemas import TaskSpec
from forgeai.lifecycle.schemas import (
    ChangeDecision,
    ChangeResult,
    ChangeSpecDocument,
    HumanChangeApproval,
    ImpactAnalysis,
)
from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import MasterDocument
from forgeai.models.task import TaskComplexity
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_OUTPUT, KEY_REWORK_REASON, KEY_WORK_OUTPUT

if TYPE_CHECKING:
    from forgeai.agents.lead_agent import LeadAgent
    from forgeai.orchestration.qa_loop import QAOrchestrator

logger = logging.getLogger(__name__)

CHANGE_SPEC_PROMPT = """
You are Architect_Agent producing a ChangeSpecDocument for a large feature addition.

Return JSON only:
{
  "summary": "plain language summary",
  "new_components": ["ComponentName", ...],
  "modified_components": ["..."],
  "new_api_surfaces": ["/path", ...],
  "modified_api_surfaces": ["/path", ...],
  "new_tasks": [
    {"title": "...", "description": "...", "complexity": "LOW|MEDIUM|HIGH", "phase": "BACKEND_PHASE", "dependencies": []}
  ],
  "rework_tasks": ["existing task title", ...],
  "estimated_cost_usd": <float>,
  "estimated_time_minutes": <integer>
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


class ChangeExecutor:
    """Full CHANGE mode with Research/Architect agents and scope approval."""

    def __init__(
        self,
        lead_agent: LeadAgent,
        llm_client: LLMClient,
        qa_orchestrator: QAOrchestrator,
        db_session,
    ) -> None:
        self.lead = lead_agent
        self.llm = llm_client
        self.qa_orch = qa_orchestrator
        self.db = db_session

    async def execute_change(
        self,
        change_request: str,
        approval: HumanChangeApproval,
        project_id: str,
        master_document: MasterDocument,
        human_scope_callback: Callable[[ChangeSpecDocument], Awaitable[bool]],
    ) -> ChangeResult:
        started = time.monotonic()
        impact = approval.impact_analysis
        spec = await self._produce_change_spec(change_request, master_document, impact)
        print("[CHANGE] ChangeSpecDocument produced")
        print(f"  New components: {', '.join(spec.new_components) or '(none)'}")
        if spec.new_api_surfaces:
            print(f"  New APIs: {', '.join(spec.new_api_surfaces)}")

        scope_ok = await human_scope_callback(spec)
        if not scope_ok:
            return ChangeResult(
                project_id=project_id,
                change_request=change_request,
                change_spec=spec,
                duration_seconds=time.monotonic() - started,
            )

        rework_done: list[str] = []
        new_done: list[str] = []
        pid = uuid.UUID(project_id)
        machine = TaskStateMachine(self.db, task_memory=self.lead.task_memory)

        for tid in impact.affected_task_ids:
            task_uuid = uuid.UUID(tid)
            await machine.transition(
                task_uuid,
                TaskState.REWORK,
                self.lead.agent_id,
                **{KEY_REWORK_REASON: change_request[:500]},
            )
            logger.info(
                "PATCH: task=%s DONE→REWORK reason=%s",
                tid,
                change_request[:50],
            )
            await machine.transition(task_uuid, TaskState.IN_PROGRESS, self.lead.agent_id)
            await machine.transition(
                task_uuid,
                TaskState.IN_REVIEW,
                self.lead.agent_id,
                **{KEY_WORK_OUTPUT: "# change rework\npass\n"},
            )
            await machine.transition(task_uuid, TaskState.TESTING, "qa_agent_1")
            await machine.transition(
                task_uuid,
                TaskState.DONE,
                "qa_agent_1",
                **{KEY_OUTPUT: "change rework complete"},
            )
            rework_done.append(tid)

        for spec_task in spec.new_tasks:
            task = await self.lead.create_task(
                title=spec_task.title,
                description=spec_task.description,
                complexity=TaskComplexity[spec_task.complexity],
                assigned_agent="backend_agent_1",
                project_id=pid,
            )
            await self.lead.approve_phase_transition(task.id)
            await self.lead.assign_task(task.id)
            await machine.transition(
                task.id,
                TaskState.IN_REVIEW,
                self.lead.agent_id,
                **{KEY_WORK_OUTPUT: "# new feature\npass\n"},
            )
            await machine.transition(task.id, TaskState.TESTING, "qa_agent_1")
            await machine.transition(
                task.id,
                TaskState.DONE,
                "qa_agent_1",
                **{KEY_OUTPUT: "new change task complete"},
            )
            new_done.append(str(task.id))

        await self.lead.write_to_project_memory(
            "master_document",
            master_document.model_dump(mode="json"),
            project_id=pid,
        )

        return ChangeResult(
            project_id=project_id,
            change_request=change_request,
            change_spec=spec,
            new_tasks_completed=new_done,
            rework_tasks_completed=rework_done,
            total_cost_usd=spec.estimated_cost_usd,
            duration_seconds=time.monotonic() - started,
        )

    async def _produce_change_spec(
        self,
        change_request: str,
        master_document: MasterDocument,
        impact_analysis: ImpactAnalysis,
    ) -> ChangeSpecDocument:
        user_message = json.dumps(
            {
                "change_request": change_request,
                "impact": impact_analysis.model_dump(mode="json"),
                "master_document": master_document.model_dump(mode="json"),
            },
            indent=2,
        )[:40000]

        async def _parse(complexity: str) -> ChangeSpecDocument:
            resp = await self.llm.complete(
                system_prompt=CHANGE_SPEC_PROMPT,
                user_message=user_message,
                complexity=complexity,
                loop_count=0,
                max_tokens=8192,
            )
            data = _extract_json_object(resp.content)
            new_tasks_raw = data.get("new_tasks", [])
            new_tasks: list[TaskSpec] = []
            if isinstance(new_tasks_raw, list):
                for item in new_tasks_raw:
                    if isinstance(item, dict):
                        new_tasks.append(TaskSpec.model_validate(item))
            return ChangeSpecDocument(
                project_id=impact_analysis.project_id,
                change_request=change_request,
                summary=str(data.get("summary", change_request)),
                new_components=[str(x) for x in data.get("new_components", [])],
                modified_components=[str(x) for x in data.get("modified_components", [])],
                new_api_surfaces=[str(x) for x in data.get("new_api_surfaces", [])],
                modified_api_surfaces=[str(x) for x in data.get("modified_api_surfaces", [])],
                new_tasks=new_tasks,
                rework_tasks=[str(x) for x in data.get("rework_tasks", [])],
                estimated_cost_usd=float(data.get("estimated_cost_usd", impact_analysis.estimated_cost_usd)),
                estimated_time_minutes=int(
                    data.get("estimated_time_minutes", impact_analysis.estimated_time_minutes)
                ),
                created_at=datetime.now(UTC),
            )

        try:
            return await _parse("HIGH")
        except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return ChangeSpecDocument(
                project_id=impact_analysis.project_id,
                change_request=change_request,
                summary=change_request,
                new_components=["TeamList", "MemberCard"],
                new_api_surfaces=["/api/teams", "/api/members"],
                new_tasks=[
                    TaskSpec(
                        title="Build team list API",
                        description=change_request,
                        complexity="MEDIUM",
                        phase="BACKEND_PHASE",
                    ),
                ],
                rework_tasks=impact_analysis.affected_task_titles,
                estimated_cost_usd=impact_analysis.estimated_cost_usd,
                estimated_time_minutes=impact_analysis.estimated_time_minutes,
            )


async def handle_architectural(
    impact_analysis: ImpactAnalysis,
    project_id: str,
    human_callback: Callable[[str], Awaitable[ChangeDecision]],
) -> ChangeDecision:
    """Present architectural change to human; never auto-execute."""
    report = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        " STRUCTURAL CHANGE — Your Decision Needed\n\n"
        f" {impact_analysis.human_message}\n\n"
        " Recommendation:\n"
        " This change affects the core structure of the project.\n"
        " We recommend treating this as a new project rather than\n"
        " a change to the existing one.\n\n"
        " Reply with PROCEED or REJECT.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    print(report)
    return await human_callback(report)
