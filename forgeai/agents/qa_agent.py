"""QA agent integrated with sandboxed test execution."""

import uuid

from forgeai.agents.base import BaseAgent
from forgeai.memory.task_memory import TaskMemory
from forgeai.exceptions import SelfApprovalError
from forgeai.models.task import Task
from forgeai.sandbox.runner import TestRunner
from forgeai.sandbox.schemas import RunnerOutput
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_DEFECT_REPORT, KEY_OUTPUT, KEY_WORK_OUTPUT


class QAAgent(BaseAgent):
    """Runs testing transitions, sandbox review, and self-approval checks."""

    def __init__(
        self,
        agent_id: str,
        db_session,
        test_runner: TestRunner | None = None,
        *,
        task_memory: TaskMemory | None = None,
    ) -> None:
        super().__init__(agent_id, db_session, task_memory=task_memory)
        self.test_runner = test_runner

    async def begin_review(self, task_id: uuid.UUID) -> Task:
        """Transition ``IN_REVIEW`` → ``TESTING``."""
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        return await machine.transition(
            task_id,
            TaskState.TESTING,
            self.agent_id,
        )

    async def review(self, task_id: uuid.UUID, code: str, test_code: str) -> RunnerOutput:
        """Run sandbox tests after enforcing no self-approval."""
        await self._assert_not_self_approval(task_id)
        if self.test_runner is None:
            raise RuntimeError("QAAgent requires a TestRunner for review()")
        return await self.test_runner.run(code=code, test_code=test_code)

    async def approve(self, task_id: uuid.UUID, output: str | None = None) -> Task:
        """Transition ``TESTING`` → ``DONE`` with provided or stored output."""
        await self._assert_not_self_approval(task_id)
        final_output = output.strip() if isinstance(output, str) and output.strip() else ""
        if not final_output:
            final_output = await self._get_work_output(task_id)
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
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
