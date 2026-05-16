"""Backend phase orchestration and API contract validation (Phase 8)."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy import select

from forgeai.escalation.ladder import EscalationLadder
from forgeai.escalation.loop_counter import LoopCounter
from forgeai.escalation.persistence import EscalationPersistence
from forgeai.llm.client import LLMClient
from forgeai.models.task import Task
from forgeai.orchestration.qa_loop import QA_FAILURE_SIGNATURE, QAOrchestrator
from forgeai.orchestration.schemas import BackendPhaseResult, ContractValidationResult, QADecision
from forgeai.sandbox.schemas import RunnerOutput
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_METADATA, KEY_WORK_OUTPUT

if TYPE_CHECKING:
    from forgeai.agents.backend_agent import BackendAgent
    from forgeai.agents.lead_agent import LeadAgent
    from forgeai.agents.qa_agent import QAAgent

logger = logging.getLogger(__name__)

CONTRACT_VALIDATION_PROMPT = """
You are QA_Agent validating Python backend code against an API contract.

Compare generated_code to api_contract for the described task.
Check:
- endpoint path matches
- HTTP method matches
- request schema alignment
- response schema alignment (required fields present)

Respond with JSON only:
{
  "valid": boolean,
  "violations": ["plain language deviation 1", ...],
  "severity": "blocking" or "warning"
}

Use severity "blocking" when path, method, or required response fields are wrong.
Use severity "warning" for minor naming or optional-field differences only.
If code matches the contract, set valid to true with an empty violations list.
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
    return str(value)


def _normalize_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [str(raw).strip()] if str(raw).strip() else []


def _normalize_contract_validation(data: dict[str, Any]) -> dict[str, Any]:
    valid = bool(data.get("valid", False))
    violations = _normalize_string_list(data.get("violations"))
    severity = _as_str(data.get("severity", "blocking")).strip().lower()
    if severity not in ("blocking", "warning"):
        severity = "blocking" if violations else "warning"
    if valid:
        violations = []
        severity = "warning"
    return {"valid": valid, "violations": violations, "severity": severity}


class ContractValidator:
    """LLM-assisted check of generated code against API_Contract."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client

    async def validate(
        self,
        generated_code: str,
        api_contract: dict,
        task_description: str,
    ) -> ContractValidationResult:
        user_message = json.dumps(
            {
                "task_description": task_description,
                "api_contract": api_contract,
                "generated_code": generated_code[:12000],
            },
            indent=2,
        )

        async def _parse(complexity: str) -> ContractValidationResult:
            resp = await self.llm.complete(
                system_prompt=CONTRACT_VALIDATION_PROMPT,
                user_message=user_message,
                complexity=complexity,
                loop_count=0,
                max_tokens=2048,
            )
            raw = _normalize_contract_validation(_extract_json_object(resp.content))
            return ContractValidationResult.model_validate(raw)

        try:
            return await _parse("LOW")
        except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return await _parse("MEDIUM")


class BackendOrchestrator:
    """Run all backend tasks through generate → contract check → QA → DONE."""

    def __init__(
        self,
        lead_agent: LeadAgent,
        backend_agent: BackendAgent,
        qa_agent: QAAgent,
        qa_orchestrator: QAOrchestrator,
        contract_validator: ContractValidator,
        db_session,
        *,
        loop_counter: LoopCounter | None = None,
        escalation_ladder: EscalationLadder | None = None,
    ) -> None:
        self.lead = lead_agent
        self.backend = backend_agent
        self.qa = qa_agent
        self.qa_orch = qa_orchestrator
        self.validator = contract_validator
        self.db = db_session
        self.loop_counter = loop_counter or LoopCounter()
        if escalation_ladder is None:
            escalation_ladder = EscalationLadder(
                self.loop_counter, EscalationPersistence(db_session)
            )
        self.escalation_ladder = escalation_ladder

    async def run_backend_phase(
        self,
        project_id: str,
        api_contract: dict,
        *,
        master_document_section: str = "",
    ) -> BackendPhaseResult:
        started = time.monotonic()
        pid = uuid.UUID(project_id)
        res = await self.db.execute(
            select(Task).where(
                Task.project_id == pid,
                Task.current_state == TaskState.TODO,
            ).order_by(Task.created_at)
        )
        tasks = [
            t
            for t in res.scalars()
            if t.assigned_agent and "backend" in t.assigned_agent.lower()
        ]

        completed: list[str] = []
        qa_cycles = 0
        contract_violations = 0
        escalations = 0
        tests_passed = 0
        tests_total = 0

        for task in tasks:
            logger.info("Backend task started: %s %s", task.id, task.title)
            section = master_document_section or (task.description or task.title)
            decision, cycles, violations, passed, total = await self._run_task_cycle(
                str(task.id),
                task.description or task.title,
                section,
                api_contract,
            )
            qa_cycles += cycles
            contract_violations += violations
            tests_passed += passed
            tests_total += total
            if decision.escalated:
                escalations += 1
            elif decision.approved:
                completed.append(str(task.id))
                logger.info(
                    "Backend task complete: %s cycles=%d",
                    task.id,
                    cycles,
                )

        return BackendPhaseResult(
            project_id=project_id,
            completed_tasks=completed,
            total_tasks=len(tasks),
            qa_cycles=qa_cycles,
            contract_violations_caught=contract_violations,
            escalations=escalations,
            phase_duration_seconds=time.monotonic() - started,
            tests_passed=tests_passed,
            tests_total=tests_total,
        )

    async def _run_task_cycle(
        self,
        task_id: str,
        task_description: str,
        master_doc_section: str,
        api_contract: dict,
    ) -> tuple[QADecision, int, int, int, int]:
        tid = uuid.UUID(task_id)
        task = await self._load_task(tid)
        if task.current_state == TaskState.TODO:
            await self.lead.assign_task(tid)

        qa_cycles = 0
        contract_violations = 0
        tests_passed = 0
        tests_total = 0
        loop_count = await self.loop_counter.get(task_id, QA_FAILURE_SIGNATURE)

        while True:
            await self.backend.complete_work(
                tid,
                task_description=task_description,
                master_document_section=master_doc_section,
                api_contract=api_contract,
                loop_count=loop_count,
            )
            code, test_code = await self._get_work_artifacts(tid)
            qa_cycles += 1
            decision = await self.lead.orchestrate_qa(
                tid,
                code,
                test_code,
                self.qa,
                self.backend.agent_id,
                "BACKEND_PHASE",
                api_contract=api_contract,
                task_description=task_description,
                loop_counter=self.loop_counter,
                escalation_ladder=self.escalation_ladder,
            )
            if decision.contract_violation:
                contract_violations += 1
                logger.warning(
                    "Contract violation: %s %s",
                    task_id,
                    decision.defect_report.failure_summary if decision.defect_report else "",
                )
            if decision.approved:
                tests_passed += decision.tests_passed
                tests_total += max(decision.tests_total, 1)
                return decision, qa_cycles, contract_violations, tests_passed, tests_total
            if decision.escalated:
                return decision, qa_cycles, contract_violations, tests_passed, tests_total
            loop_count = await self.loop_counter.get(task_id, QA_FAILURE_SIGNATURE)

    async def _load_task(self, task_id: uuid.UUID) -> Task:
        res = await self.db.execute(select(Task).where(Task.id == task_id))
        task = res.scalar_one_or_none()
        if task is None:
            raise RuntimeError(f"Task not found: {task_id}")
        return task

    async def _get_work_artifacts(self, task_id: uuid.UUID) -> tuple[str, str]:
        hist = TaskStateMachine(self.db, task_memory=self.lead.task_memory)
        rows = await hist.get_history(task_id)
        for row in reversed(rows):
            if (
                row.success
                and row.from_state == TaskState.IN_PROGRESS
                and row.to_state == TaskState.IN_REVIEW
            ):
                meta = row.metadata_ or {}
                code = str(meta.get(KEY_WORK_OUTPUT, ""))
                extra = meta.get(KEY_METADATA) or {}
                test_code = str(extra.get("test_code") or "")
                if not test_code.strip():
                    test_code = (
                        "def test_module_imports():\n"
                        "    import main\n"
                        "    assert main is not None\n"
                    )
                return code, test_code
        raise RuntimeError(f"No work output for task {task_id}")
