"""Full QA approval/rejection orchestration (Phase 7, Req 06)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from forgeai.escalation.ladder import EscalationLadder
from forgeai.escalation.loop_counter import LoopCounter
from forgeai.escalation.schemas import EscalationResult
from forgeai.llm.client import LLMClient
from forgeai.memory.task_memory import TaskMemory
from forgeai.models.task import Task
from forgeai.orchestration.schemas import DefectReport, QADecision
from forgeai.sandbox.schemas import RunnerOutput
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_DEFECT_REPORT, KEY_OUTPUT, KEY_WORK_OUTPUT

logger = logging.getLogger(__name__)

QA_FAILURE_SIGNATURE = "qa_failure"

DEFECT_REPORT_GENERATION_PROMPT = """
You are QA_Agent analyzing test failures.

Failed tests: {failed_test_names}
Test output: {stdout}
Error details: {stderr}

Produce a structured defect report with:
1. failure_summary: one plain-language sentence describing the core problem
2. suggestions: specific actionable steps the developer should take to fix it
3. failed_tests: list of test names that failed
4. passed_tests: list of test names that passed

Output JSON only.
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


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalize_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [str(raw).strip()] if str(raw).strip() else []


def _normalize_defect_payload(
    data: dict[str, Any],
    *,
    task_id: str,
    qa_agent_id: str,
    original_agent_id: str,
    execution_mode: str,
    retry_count: int,
    runner_output: RunnerOutput,
) -> dict[str, Any]:
    failed = _normalize_string_list(data.get("failed_tests"))
    passed = _normalize_string_list(data.get("passed_tests"))
    if not failed:
        failed = [c.name for c in runner_output.test_cases if not c.passed]
    if not passed:
        passed = [c.name for c in runner_output.test_cases if c.passed]
    summary = _as_str(data.get("failure_summary")).strip()
    if not summary:
        summary = (
            runner_output.sandbox_error.strip()
            or runner_output.stderr.strip()
            or "Tests failed"
        )
    suggestions = _as_str(data.get("suggestions")).strip()
    if not suggestions:
        suggestions = "Review failing tests and align implementation with acceptance criteria."
    return {
        "task_id": task_id,
        "agent_id": qa_agent_id,
        "original_agent_id": original_agent_id,
        "failure_summary": summary,
        "failed_tests": failed,
        "passed_tests": passed,
        "execution_mode": execution_mode,
        "suggestions": suggestions,
        "retry_count": retry_count,
        "created_at": datetime.now(UTC),
    }


class QAOrchestrator:
    """Approve or reject QA results; coordinate defect reports and escalation."""

    def __init__(
        self,
        state_machine: TaskStateMachine,
        loop_counter: LoopCounter,
        escalation_ladder: EscalationLadder,
        llm_client: LLMClient,
        db_session,
        *,
        task_memory: TaskMemory | None = None,
    ) -> None:
        self.sm = state_machine
        self.loop_counter = loop_counter
        self.escalation = escalation_ladder
        self.llm = llm_client
        self.db = db_session
        self.task_memory = task_memory

    async def process_result(
        self,
        task_id: str,
        runner_output: RunnerOutput,
        qa_agent_id: str,
        original_agent_id: str,
        development_phase: str,
    ) -> QADecision:
        tid = uuid.UUID(task_id)
        if runner_output.success:
            await self._approve(tid, qa_agent_id)
            return QADecision(task_id=task_id, approved=True)

        defect_report = await self._generate_defect_report(
            task_id,
            runner_output,
            qa_agent_id,
            original_agent_id,
            development_phase,
        )

        loop_count = await self.loop_counter.get(task_id, QA_FAILURE_SIGNATURE)
        if loop_count >= 3:
            logger.error("QA escalating: task=%s loop_count=3", task_id)
            escalation_result = await self.escalation.escalate(
                task_id=task_id,
                agent_id=qa_agent_id,
                error_signature=QA_FAILURE_SIGNATURE,
                error_detail=defect_report.failure_summary,
                task_specification=defect_report.suggestions,
            )
            return QADecision(
                task_id=task_id,
                approved=False,
                defect_report=defect_report,
                escalated=True,
                escalation_result=escalation_result,
            )

        await self._reject(tid, qa_agent_id, defect_report)
        await self._reassign_to_original_agent(task_id, original_agent_id, defect_report)
        return QADecision(
            task_id=task_id,
            approved=False,
            defect_report=defect_report,
        )

    async def _approve(self, task_id: uuid.UUID, qa_agent_id: str) -> None:
        output = await self._resolve_output(task_id)
        await self.sm.transition(
            task_id,
            TaskState.DONE,
            qa_agent_id,
            **{KEY_OUTPUT: output},
        )
        await self.loop_counter.reset(str(task_id))
        task = await self._load_task(task_id)
        await self._write_task_output_to_project_memory(task, output)
        logger.info("QA approved: task=%s agent=%s", task_id, qa_agent_id)

    async def _reject(
        self,
        task_id: uuid.UUID,
        qa_agent_id: str,
        defect_report: DefectReport,
    ) -> None:
        report_text = (
            f"{defect_report.failure_summary}\n\n"
            f"Suggestions:\n{defect_report.suggestions}"
        )
        await self.sm.transition(
            task_id,
            TaskState.IN_PROGRESS,
            qa_agent_id,
            **{KEY_DEFECT_REPORT: report_text},
        )
        new_count = await self.loop_counter.increment(str(task_id), QA_FAILURE_SIGNATURE)
        logger.warning(
            "QA rejected: task=%s attempt=%d",
            task_id,
            new_count,
        )

    async def _generate_defect_report(
        self,
        task_id: str,
        runner_output: RunnerOutput,
        qa_agent_id: str,
        original_agent_id: str,
        development_phase: str,
    ) -> DefectReport:
        execution_mode = (
            "playwright" if development_phase == "FRONTEND_PHASE" else "pytest"
        )
        retry_count = await self.loop_counter.get(task_id, QA_FAILURE_SIGNATURE)
        failed_names = [c.name for c in runner_output.test_cases if not c.passed]
        user_message = DEFECT_REPORT_GENERATION_PROMPT.format(
            failed_test_names=", ".join(failed_names) or "(none parsed)",
            stdout=runner_output.stdout[:8000],
            stderr=runner_output.stderr[:8000],
        )

        async def _parse(complexity: str) -> DefectReport:
            resp = await self.llm.complete(
                system_prompt="You are QA_Agent. Output JSON only.",
                user_message=user_message,
                complexity=complexity,
                loop_count=retry_count,
                max_tokens=2048,
            )
            raw = _normalize_defect_payload(
                _extract_json_object(resp.content),
                task_id=task_id,
                qa_agent_id=qa_agent_id,
                original_agent_id=original_agent_id,
                execution_mode=execution_mode,
                retry_count=retry_count,
                runner_output=runner_output,
            )
            return DefectReport.model_validate(raw)

        try:
            return await _parse("LOW")
        except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return await _parse("MEDIUM")

    async def _reassign_to_original_agent(
        self,
        task_id: str,
        original_agent_id: str,
        defect_report: DefectReport,
    ) -> None:
        if self.task_memory is not None:
            await self.task_memory.set(
                task_id,
                "defect_report",
                defect_report.model_dump_json(),
            )
        logger.info(
            "QA reassigned task=%s to original_agent=%s",
            task_id,
            original_agent_id,
        )

    async def _resolve_output(self, task_id: uuid.UUID) -> str:
        hist = await self.sm.get_history(task_id)
        for row in reversed(hist):
            if (
                row.success
                and row.from_state == TaskState.IN_PROGRESS
                and row.to_state == TaskState.IN_REVIEW
            ):
                meta = row.metadata_ or {}
                out = meta.get(KEY_WORK_OUTPUT)
                if isinstance(out, str) and out.strip():
                    return out.strip()
        task = await self._load_task(task_id)
        if task.output and task.output.strip():
            return task.output.strip()
        return "QA approved"

    async def _load_task(self, task_id: uuid.UUID) -> Task:
        res = await self.db.execute(select(Task).where(Task.id == task_id))
        task = res.scalar_one_or_none()
        if task is None:
            raise RuntimeError(f"Task not found: {task_id}")
        return task

    async def _write_task_output_to_project_memory(self, task: Task, output: str) -> None:
        from forgeai.models.project_artefact import ProjectArtefactModel

        row = ProjectArtefactModel(
            project_id=task.project_id,
            artefact_type=f"task_output:{task.id}",
            content={"task_id": str(task.id), "title": task.title, "output": output},
            version=1,
            is_current=True,
            created_by="qa_orchestrator",
        )
        self.db.add(row)
        await self.db.commit()
