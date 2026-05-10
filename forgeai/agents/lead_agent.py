"""Lead agent stub: task creation and early lifecycle transitions."""

import uuid

from forgeai.agents.base import BaseAgent
from forgeai.models.task import Task, TaskComplexity
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_PHASE_APPROVAL


class LeadAgent(BaseAgent):
    """Creates tasks and performs lead-side transitions."""

    async def create_task(
        self,
        title: str,
        description: str | None,
        complexity: TaskComplexity,
        assigned_agent: str,
        project_id: uuid.UUID | None = None,
    ) -> Task:
        """Insert a new task in ``PHASE_LOCKED`` state.

        Args:
            title: Human-readable title.
            description: Optional longer description.
            complexity: LOW/MEDIUM/HIGH.
            assigned_agent: Agent id string assigned to execute the task.
            project_id: Optional project scope; random UUID if omitted.

        Returns:
            The persisted ``Task``.

        Raises:
            sqlalchemy.exc.SQLAlchemyError: On persistence failures.
        """
        pid = project_id or uuid.uuid4()
        task = Task(
            project_id=pid,
            title=title,
            description=description,
            assigned_agent=assigned_agent,
            complexity=complexity,
            current_state=TaskState.PHASE_LOCKED,
        )
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def approve_phase_transition(self, task_id: uuid.UUID) -> Task:
        """Move ``PHASE_LOCKED`` → ``TODO`` with approval.

        Args:
            task_id: Target task id.

        Returns:
            Updated task after transition.

        Raises:
            forgeai.exceptions.InvalidTransitionError: If the edge is invalid.
            forgeai.exceptions.TransitionConditionError: If approval is missing.
        """
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        return await machine.transition(
            task_id,
            TaskState.TODO,
            self.agent_id,
            **{KEY_PHASE_APPROVAL: True},
        )

    async def assign_task(self, task_id: uuid.UUID) -> Task:
        """Move ``TODO`` → ``IN_PROGRESS``.

        Args:
            task_id: Target task id.

        Returns:
            Updated task after transition.

        Raises:
            forgeai.exceptions.InvalidTransitionError: If the edge is invalid.
            forgeai.exceptions.TransitionConditionError: On condition failures.
        """
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        return await machine.transition(
            task_id,
            TaskState.IN_PROGRESS,
            self.agent_id,
        )
