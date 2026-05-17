"""QA agent integrated with sandboxed test execution."""

import asyncio
import json
import re
import subprocess
import uuid
from pathlib import Path

from forgeai.agents.base import BaseAgent
from forgeai.contracts.schemas import NavigationContract, PageSpec
from forgeai.llm.client import LLMClient
from forgeai.memory.task_memory import TaskMemory
from sqlalchemy import select

from forgeai.exceptions import (
    InvalidTransitionError,
    SandboxProvisionError,
    SandboxTimeoutError,
    SelfApprovalError,
)
from forgeai.models.task import Task
from forgeai.sandbox.frontend_sandbox import FrontendSandbox
from forgeai.sandbox.runner import TestRunner
from forgeai.orchestration.backend_orchestrator import ContractValidator
from forgeai.sandbox.schemas import RunnerOutput
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_DEFECT_REPORT, KEY_OUTPUT, KEY_WORK_OUTPUT

QA_DEFECT_ANALYSIS_PROMPT = """
You are QA_Agent. Given a task description and pytest/sandbox output, write a concise
defect report for the developer: bullet points for each failure, likely root cause,
and concrete fix hints. Output plain text only.
""".strip()

QA_PLAYWRIGHT_GENERATION_PROMPT = """
You are QA_Agent. Generate a single Playwright test file (JavaScript) for the page
described in the PageSpec. Use @playwright/test with test() and expect().

Requirements:
- Import: import { test, expect } from '@playwright/test';
- Use test.describe with the page name.
- Include a test that page.goto(route) succeeds and the document has no obvious error title.
- For each section in PageSpec.sections, add a test that a plausible selector for that
  section is visible (use role/name text, data-testid derived from the section slug, or
  getByRole where appropriate).
- For each interaction in PageSpec.interactions, add a minimal test that exercises the UI
  if selectors can be inferred; otherwise assert the page is still interactive.
- Include at least one test that verifies navigation links match the NavigationContract routes
  (check href or router links for known paths).
- Output ONLY the JavaScript source code. No markdown fences, no commentary outside the file.
""".strip()


def _strip_js_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s.strip()


class QAAgent(BaseAgent):
    """Runs testing transitions, sandbox review, and self-approval checks."""

    def __init__(
        self,
        agent_id: str,
        db_session,
        test_runner: TestRunner | None = None,
        *,
        task_memory: TaskMemory | None = None,
        llm_client: LLMClient | None = None,
        frontend_sandbox: FrontendSandbox | None = None,
        contract_validator: ContractValidator | None = None,
    ) -> None:
        super().__init__(agent_id, db_session, task_memory=task_memory)
        self.test_runner = test_runner
        self.llm = llm_client
        self.frontend_sandbox = frontend_sandbox
        self.contract_validator = contract_validator

    async def begin_review(self, task_id: uuid.UUID) -> Task:
        """Transition ``IN_REVIEW`` → ``TESTING`` (idempotent if already ``TESTING``)."""
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        task = await self._load_task(task_id)
        if task.current_state == TaskState.TESTING:
            return task
        if task.current_state == TaskState.IN_REVIEW:
            return await machine.transition(
                task_id,
                TaskState.TESTING,
                self.agent_id,
            )
        raise InvalidTransitionError(
            f"begin_review requires IN_REVIEW or TESTING, got {task.current_state.value}"
        )

    async def review(
        self,
        task_id: uuid.UUID,
        code: str,
        test_code: str,
        development_phase: str = "BACKEND_PHASE",
        *,
        api_contract: dict | None = None,
        task_description: str | None = None,
    ) -> RunnerOutput:
        """Run sandbox tests after enforcing no self-approval."""
        await self._assert_not_self_approval(task_id)
        if development_phase == "FRONTEND_PHASE":
            return await self._run_playwright(code, test_code)
        if (
            development_phase == "BACKEND_PHASE"
            and api_contract
            and self.contract_validator is not None
        ):
            validation = await self.contract_validator.validate(
                code,
                api_contract,
                task_description or "",
            )
            if not validation.valid and validation.severity == "blocking":
                violation_text = "\n".join(validation.violations)
                return RunnerOutput(
                    success=False,
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=1,
                    test_cases=[],
                    stdout="",
                    stderr=violation_text,
                    execution_time_seconds=0.0,
                    sandbox_error="API contract violation: " + ", ".join(validation.violations),
                )
        if self.test_runner is None:
            raise RuntimeError("QAAgent requires a TestRunner for review()")
        return await self.test_runner.run(code=code, test_code=test_code)

    async def _run_playwright(self, code: str, test_code: str) -> RunnerOutput:
        if self.frontend_sandbox is None:
            raise RuntimeError("QAAgent requires FrontendSandbox for FRONTEND_PHASE review()")
        try:
            return await self.frontend_sandbox.run(component_code=code, test_code=test_code)
        except SandboxTimeoutError as exc:
            return RunnerOutput(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                test_cases=[],
                stdout="",
                stderr="",
                execution_time_seconds=0.0,
                timed_out=True,
                sandbox_error=str(exc),
            )
        except SandboxProvisionError as exc:
            return RunnerOutput(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                test_cases=[],
                stdout="",
                stderr="",
                execution_time_seconds=0.0,
                timed_out=False,
                sandbox_error=str(exc),
            )

    async def generate_playwright_tests(
        self,
        page_spec: PageSpec,
        navigation_contract: NavigationContract,
    ) -> str:
        """Generate a Playwright test module from layout and navigation context."""
        if self.llm is None:
            raise RuntimeError("QAAgent requires llm_client for generate_playwright_tests()")

        user_payload = {
            "page_spec": page_spec.model_dump(mode="json"),
            "navigation_contract": navigation_contract.model_dump(mode="json"),
        }
        user_message = json.dumps(user_payload, indent=2)

        async def _call(complexity: str) -> str:
            resp = await self.llm.complete(
                system_prompt=QA_PLAYWRIGHT_GENERATION_PROMPT,
                user_message=user_message,
                complexity=complexity,
                loop_count=0,
                max_tokens=8192,
            )
            return _strip_js_fence(resp.content)

        text = await _call("LOW")
        if self._validate_generated_playwright(page_spec, navigation_contract, text):
            return text
        text = await _call("MEDIUM")
        if self._validate_generated_playwright(page_spec, navigation_contract, text):
            return text
        return text

    def _validate_generated_playwright(
        self,
        page_spec: PageSpec,
        navigation_contract: NavigationContract,
        text: str,
    ) -> bool:
        if "@playwright/test" not in text or "test(" not in text:
            return False
        if page_spec.route and page_spec.route not in text:
            return False
        if page_spec.sections:
            lowered = text.lower()
            hits = 0
            for section in page_spec.sections:
                slug = re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")
                tokens = [section.lower(), slug, slug.replace("-", " ")]
                if any(t and t in lowered for t in tokens):
                    hits += 1
            if hits == 0:
                return False
        if navigation_contract.routes:
            route_hits = 0
            for r in navigation_contract.routes:
                if r.path and r.path in text:
                    route_hits += 1
            if route_hits == 0:
                return False
        return True

    async def validate_docker_build(self, output_dir: str) -> bool:
        """Build the deployment Dockerfile in ``output_dir`` (Phase 10)."""
        root = Path(output_dir)
        if not (root / "Dockerfile").is_file():
            return False

        def _build() -> bool:
            proc = subprocess.run(
                ["docker", "build", "-t", "forgeai-delivery-verify", "."],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=300,
            )
            return proc.returncode == 0

        try:
            return await asyncio.to_thread(_build)
        except (OSError, subprocess.TimeoutExpired):
            return False

    async def analyze_defects(self, task_specification: str, runner_output: RunnerOutput) -> str:
        """Optional LLM-assisted defect narrative from sandbox results."""
        if self.llm is None:
            return (
                runner_output.sandbox_error.strip()
                or runner_output.stderr.strip()
                or "Tests failed"
            )
        detail = (
            f"Task:\n{task_specification}\n\n"
            f"success={runner_output.success} timed_out={runner_output.timed_out}\n"
            f"stdout:\n{runner_output.stdout}\n\nstderr:\n{runner_output.stderr}\n\n"
            f"sandbox_error:\n{runner_output.sandbox_error}"
        )
        resp = await self.llm.complete(
            system_prompt=QA_DEFECT_ANALYSIS_PROMPT,
            user_message=detail,
            complexity="LOW",
            loop_count=0,
            max_tokens=2048,
        )
        return resp.content.strip()

    async def approve(self, task_id: uuid.UUID, output: str | None = None) -> Task:
        """Transition to ``DONE`` via ``IN_REVIEW`` → ``TESTING`` → ``DONE``.

        Fills in earlier states when lenient approval runs after QA rejection
        (``IN_PROGRESS``) or when ``begin_review`` was skipped (``IN_REVIEW``).
        """
        await self._assert_not_self_approval(task_id)
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        task = await self._load_task(task_id)
        final_output = output.strip() if isinstance(output, str) and output.strip() else ""
        if not final_output:
            try:
                final_output = await self._get_work_output(task_id)
            except RuntimeError:
                final_output = "QA approved"
        if task.current_state == TaskState.IN_PROGRESS:
            task = await machine.transition(
                task_id,
                TaskState.IN_REVIEW,
                task.assigned_agent,
                **{KEY_WORK_OUTPUT: final_output},
            )
        if task.current_state == TaskState.IN_REVIEW:
            task = await machine.transition(
                task_id,
                TaskState.TESTING,
                self.agent_id,
            )
        elif task.current_state != TaskState.TESTING:
            raise InvalidTransitionError(
                f"approve requires TESTING (or IN_REVIEW/IN_PROGRESS), got {task.current_state.value}"
            )
        return await machine.transition(
            task_id,
            TaskState.DONE,
            self.agent_id,
            **{KEY_OUTPUT: final_output},
        )

    async def reject(self, task_id: uuid.UUID, defect_report: str) -> Task:
        """Transition ``TESTING`` → ``IN_PROGRESS`` with a defect report.

        Args:
            task_id: Task failing QA.
            defect_report: Non-empty explanation of defects.

        Returns:
            Updated task after transition.

        Raises:
            SelfApprovalError: If QA id matches the implementer id.
            forgeai.exceptions.InvalidTransitionError: If the edge is invalid.
            forgeai.exceptions.TransitionConditionError: If defect report invalid.
        """
        await self._assert_not_self_approval(task_id)
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        return await machine.transition(
            task_id,
            TaskState.IN_PROGRESS,
            self.agent_id,
            **{KEY_DEFECT_REPORT: defect_report},
        )

    async def _load_task(self, task_id: uuid.UUID) -> Task:
        res = await self.db.execute(select(Task).where(Task.id == task_id))
        task = res.scalar_one_or_none()
        if task is None:
            raise RuntimeError(f"Task not found: {task_id}")
        return task

    async def _get_work_output(self, task_id: uuid.UUID) -> str:
        """Return work output captured at ``IN_PROGRESS`` → ``IN_REVIEW``.

        Args:
            task_id: Task id.

        Returns:
            Non-empty work output string.

        Raises:
            RuntimeError: If no prior work output row exists.
        """
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        hist = await machine.get_history(task_id)
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
        raise RuntimeError("No work output found for task from prior transition")

    async def _assert_not_self_approval(self, task_id: uuid.UUID) -> None:
        """Ensure QA is not the same agent that completed implementation.

        Args:
            task_id: Task under QA.

        Raises:
            SelfApprovalError: If implementer id equals this QA ``agent_id``.
        """
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        hist = await machine.get_history(task_id)
        for row in reversed(hist):
            if (
                row.success
                and row.from_state == TaskState.IN_PROGRESS
                and row.to_state == TaskState.IN_REVIEW
            ):
                if row.agent_id == self.agent_id:
                    raise SelfApprovalError(
                        "QA cannot act on work produced by the same agent_id"
                    )
                return
        # No implementer row yet — nothing to enforce for self-approval.
        return
