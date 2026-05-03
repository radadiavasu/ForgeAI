"""QA agent stub with self-approval prevention."""

import uuid

from forgeai.agents.base import BaseAgent
from forgeai.exceptions import SelfApprovalError
from forgeai.models.task import Task
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_DEFECT_REPORT, KEY_OUTPUT, KEY_WORK_OUTPUT


class QAAgent(BaseAgent):
    """Runs testing transitions and enforces no self-approval."""

    async def begin_review(self, task_id: uuid.UUID) -> Task:
        """Transition ``IN_REVIEW`` → ``TESTING``.

        Self-approval is enforced on ``approve`` / ``reject``, not on this hand-off,
        so integration tests can reach ``TESTING`` before a blocked ``approve``.

        Args:
            task_id: Task entering QA testing.

        Returns:
            Updated task after transition.

        Raises:
            forgeai.exceptions.InvalidTransitionError: If the edge is invalid.
            forgeai.exceptions.TransitionConditionError: On condition failures.
        """
        machine = TaskStateMachine(self.db)
        return await machine.transition(
            task_id,
            TaskState.TESTING,
            self.agent_id,
        )

    async def approve(self, task_id: uuid.UUID) -> Task:
        """Transition ``TESTING`` → ``DONE`` using stored work output.

        Args:
            task_id: Task to approve.

        Returns:
            Updated task including ``output`` set for DONE.

        Raises:
            SelfApprovalError: If QA id matches the implementer id.
            forgeai.exceptions.InvalidTransitionError: If the edge is invalid.
            forgeai.exceptions.TransitionConditionError: If DONE output missing.
        """
        await self._assert_not_self_approval(task_id)
        work_out = await self._get_work_output(task_id)
        machine = TaskStateMachine(self.db)
        return await machine.transition(
            task_id,
            TaskState.DONE,
            self.agent_id,
            **{KEY_OUTPUT: work_out},
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
        machine = TaskStateMachine(self.db)
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
        machine = TaskStateMachine(self.db)
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
        machine = TaskStateMachine(self.db)
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
