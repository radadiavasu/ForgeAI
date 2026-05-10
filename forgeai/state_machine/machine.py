"""Task state machine: transition orchestration and audit logging."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.exceptions import (
    ForgeAIError,
    InvalidTransitionError,
    TransitionConditionError,
)
from forgeai.memory.task_memory import TaskMemory
from forgeai.models.task import Task, TaskStateHistory
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import (
    KEY_DEFECT_REPORT,
    KEY_METADATA,
    KEY_OUTPUT,
    KEY_PHASE_APPROVAL,
    KEY_REWORK_REASON,
    KEY_WORK_OUTPUT,
    get_transition_def,
    validate_conditions,
)

logger = logging.getLogger(__name__)


class TaskStateMachine:
    """Applies allowed transitions, persists audit rows, and updates tasks."""

    def __init__(
        self,
        session: AsyncSession,
        task_memory: TaskMemory | None = None,
    ) -> None:
        """Attach an async session used for all operations.

        Args:
            session: Active SQLAlchemy async session.
            task_memory: Optional Redis task memory; defaults from settings when omitted.
        """
        self._session = session
        self._task_memory = task_memory

    async def transition(
        self,
        task_id: UUID,
        to_state: TaskState,
        agent_id: str,
        **kwargs: Any,
    ) -> Task:
        """Move a task to ``to_state`` if allowed and conditions pass.

        Args:
            task_id: Primary key of the task.
            to_state: Requested next state.
            agent_id: Identifier of the agent performing the transition.
            **kwargs: Transition-specific parameters (approval flags, text fields).

        Returns:
            The updated ``Task`` after a successful commit.

        Raises:
            InvalidTransitionError: If the edge is not in the permitted map.
            TransitionConditionError: If the edge is allowed but conditions fail.
        """
        task = await self._load_task(task_id)
        from_state = task.current_state
        now = datetime.now(UTC)
        tdef = get_transition_def(from_state, to_state)

        if tdef is None:
            self._log_violation(agent_id, from_state, to_state, task_id, now)
            await self._record_failure(
                task=task,
                agent_id=agent_id,
                from_state=from_state,
                to_state=to_state,
                reason="transition not permitted",
            )
            await self._session.commit()
            raise InvalidTransitionError(
                f"Transition {from_state.value} → {to_state.value} is not permitted"
            )

        cond_err = validate_conditions(tdef, kwargs)
        if cond_err is not None:
            logger.warning(
                "Transition condition failed: agent=%s task=%s %s→%s reason=%s",
                agent_id,
                task_id,
                from_state.value,
                to_state.value,
                cond_err,
            )
            await self._record_failure(
                task=task,
                agent_id=agent_id,
                from_state=from_state,
                to_state=to_state,
                reason=cond_err,
            )
            await self._session.commit()
            raise TransitionConditionError(cond_err)

        extra_meta: dict[str, Any] = {}
        if kwargs.get(KEY_WORK_OUTPUT) is not None:
            extra_meta[KEY_WORK_OUTPUT] = kwargs[KEY_WORK_OUTPUT]
        if kwargs.get(KEY_METADATA) is not None and isinstance(kwargs[KEY_METADATA], dict):
            extra_meta.update(kwargs[KEY_METADATA])
        if kwargs.get(KEY_REWORK_REASON) is not None:
            extra_meta[KEY_REWORK_REASON] = kwargs[KEY_REWORK_REASON]

        defect_col: str | None = None
        if tdef.to_state == TaskState.IN_PROGRESS and from_state == TaskState.TESTING:
            defect_col = str(kwargs[KEY_DEFECT_REPORT])

        if to_state == TaskState.DONE:
            output_val = kwargs.get(KEY_OUTPUT)
            if not isinstance(output_val, str) or not output_val.strip():
                msg = "output must be a non-empty string when transitioning to DONE"
                logger.warning(
                    "Transition condition failed: agent=%s task=%s %s→%s reason=%s",
                    agent_id,
                    task_id,
                    from_state.value,
                    to_state.value,
                    msg,
                )
                await self._record_failure(
                    task=task,
                    agent_id=agent_id,
                    from_state=from_state,
                    to_state=to_state,
                    reason=msg,
                )
                await self._session.commit()
                raise TransitionConditionError(msg)
            task.output = output_val.strip()

        task.current_state = to_state
        task.updated_at = datetime.now(UTC)

        history_meta = extra_meta if extra_meta else None
        success_row = TaskStateHistory(
            task_id=task.id,
            agent_id=agent_id,
            from_state=from_state,
            to_state=to_state,
            success=True,
            rejection_reason=None,
            defect_report=defect_col,
            metadata_=history_meta,
        )
        self._session.add(success_row)

        logger.info(
            "Transition ok: agent=%s task=%s %s→%s",
            agent_id,
            task_id,
            from_state.value,
            to_state.value,
        )

        await self._session.commit()
        await self._session.refresh(task)

        if to_state == TaskState.DONE:
            tm = self._task_memory if self._task_memory is not None else TaskMemory.from_settings()
            await tm.delete_all(str(task_id))

        return task

    async def get_history(self, task_id: UUID) -> list[TaskStateHistory]:
        """Return ordered state history for a task (oldest first, newest last).

        Args:
            task_id: Task primary key.

        Returns:
            Chronological list of ``TaskStateHistory`` rows.
        """
        result = await self._session.execute(
            select(TaskStateHistory)
            .where(TaskStateHistory.task_id == task_id)
            .order_by(TaskStateHistory.attempted_at.asc())
        )
        return list(result.scalars().all())

    async def _load_task(self, task_id: UUID) -> Task:
        """Load a task by id or raise if missing.

        Args:
            task_id: Task primary key.

        Returns:
            The ``Task`` instance.

        Raises:
            ForgeAIError: If no task exists for ``task_id``.
        """
        res = await self._session.execute(select(Task).where(Task.id == task_id))
        task = res.scalar_one_or_none()
        if task is None:
            raise ForgeAIError(f"Task not found: {task_id}")
        return task

    def _log_violation(
        self,
        agent_id: str,
        from_state: TaskState,
        to_state: TaskState,
        task_id: UUID,
        timestamp: datetime,
    ) -> None:
        """Log a disallowed transition attempt at WARNING level."""
        logger.warning(
            "Invalid transition: agent_id=%s from_state=%s to_state=%s task_id=%s "
            "timestamp=%s",
            agent_id,
            from_state.value,
            to_state.value,
            task_id,
            timestamp.isoformat(),
        )

    async def _record_failure(
        self,
        *,
        task: Task,
        agent_id: str,
        from_state: TaskState,
        to_state: TaskState,
        reason: str,
    ) -> None:
        """Append a failed audit row without changing ``task.current_state``."""
        row = TaskStateHistory(
            task_id=task.id,
            agent_id=agent_id,
            from_state=from_state,
            to_state=to_state,
            success=False,
            rejection_reason=reason,
            defect_report=None,
            metadata_=None,
        )
        self._session.add(row)
        logger.warning(
            "Transition rejected: agent=%s task=%s %s→%s reason=%s",
            agent_id,
            task.id,
            from_state.value,
            to_state.value,
            reason,
        )
