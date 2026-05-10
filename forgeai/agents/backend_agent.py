"""Backend developer agent stub."""

import uuid

from forgeai.agents.base import BaseAgent
from forgeai.models.task import Task
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_WORK_OUTPUT


class BackendAgent(BaseAgent):
    """Simulates backend work and hand-off to review."""

    async def complete_work(self, task_id: uuid.UUID, output: str) -> Task:
        """Transition ``IN_PROGRESS`` → ``IN_REVIEW`` and record work output.

        The output is stored on the success history row metadata for later QA.

        Args:
            task_id: Task being completed.
            output: Work summary string attached to the transition metadata.

        Returns:
            Updated task after transition.

        Raises:
            forgeai.exceptions.InvalidTransitionError: If the edge is invalid.
            forgeai.exceptions.TransitionConditionError: On condition failures.
        """
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        return await machine.transition(
            task_id,
            TaskState.IN_REVIEW,
            self.agent_id,
            **{KEY_WORK_OUTPUT: output},
        )
