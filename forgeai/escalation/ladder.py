"""Escalation ladder implementing five-level failure handling."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from forgeai.escalation.loop_counter import LoopCounter
from forgeai.escalation.schemas import EscalationEvent, EscalationLevel, EscalationResult
from forgeai.exceptions import AlreadyEscalatedError

logger = logging.getLogger(__name__)


class EscalationLadder:
    """Failure escalation flow from self-retry to human intervention."""

    def __init__(self, loop_counter: LoopCounter, max_self_retries: int = 2) -> None:
        self.loop_counter = loop_counter
        self.max_self_retries = max_self_retries
        self._events: list[EscalationEvent] = []
        self._self_retry_attempts: dict[str, int] = {}
        # Phase 4 will persist events to PostgreSQL.

    async def escalate(
        self,
        task_id: str,
        agent_id: str,
        error_signature: str,
        error_detail: str,
        task_specification: str,
    ) -> EscalationResult:
        """Run the escalation decision tree and return the final result."""
        if self.get_current_level(task_id) == EscalationLevel.HUMAN_INPUT:
            raise AlreadyEscalatedError(
                f"Task {task_id} already reached level 5 and requires human input"
            )

        loop_count = self.loop_counter.increment(task_id, error_signature)
        start_level = EscalationLevel.PEER_ASSIST if loop_count >= 3 else EscalationLevel.SELF_RETRY

        if start_level == EscalationLevel.PEER_ASSIST:
            logger.warning(
                "Same error seen 3+ times: task_id=%s error_signature=%s loop_count=%d",
                task_id,
                error_signature,
                loop_count,
            )

        if start_level == EscalationLevel.SELF_RETRY:
            resolved = await self._level_1_self_retry(task_id=task_id, agent_id=agent_id)
            self._record_event(
                task_id=task_id,
                agent_id=agent_id,
                level=EscalationLevel.SELF_RETRY,
                error_signature=error_signature,
                error_detail=error_detail,
                loop_count=loop_count,
                resolved=resolved,
                resolution="Self-retry succeeded with alternate approach"
                if resolved
                else "Self-retry failed; retries exhausted or unresolved",
            )
            if resolved:
                return EscalationResult(
                    level_reached=EscalationLevel.SELF_RETRY,
                    resolved=True,
                    resolution="Issue resolved at Level 1 via self-retry",
                    needs_human_input=False,
                )

        level2 = await self._level_2_peer_assist(task_id=task_id, agent_id=agent_id)
        self._record_event(
            task_id=task_id,
            agent_id=agent_id,
            level=EscalationLevel.PEER_ASSIST,
            error_signature=error_signature,
            error_detail=error_detail,
            loop_count=loop_count,
            resolved=level2,
            resolution="Peer assist resolved the issue" if level2 else "Peer assist failed",
        )
        if level2:
            return EscalationResult(
                level_reached=EscalationLevel.PEER_ASSIST,
                resolved=True,
                resolution="Issue resolved at Level 2 by peer assist",
                needs_human_input=False,
            )

        level3 = await self._level_3_architect_review(
            task_id=task_id, task_specification=task_specification
        )
        self._record_event(
            task_id=task_id,
            agent_id=agent_id,
            level=EscalationLevel.ARCHITECT_REVIEW,
            error_signature=error_signature,
            error_detail=error_detail,
            loop_count=loop_count,
            resolved=level3,
            resolution="Architect review resolved ambiguity" if level3 else "Architect review failed",
        )
        if level3:
            return EscalationResult(
                level_reached=EscalationLevel.ARCHITECT_REVIEW,
                resolved=True,
                resolution="Issue resolved at Level 3 by architect review",
                needs_human_input=False,
            )

        level4 = await self._level_4_task_rewrite(
            task_id=task_id, task_specification=task_specification
        )
        self._record_event(
            task_id=task_id,
            agent_id=agent_id,
            level=EscalationLevel.TASK_REWRITE,
            error_signature=error_signature,
            error_detail=error_detail,
            loop_count=loop_count,
            resolved=level4,
            resolution="Task rewrite resolved execution issues" if level4 else "Task rewrite failed",
        )
        if level4:
            return EscalationResult(
                level_reached=EscalationLevel.TASK_REWRITE,
                resolved=True,
                resolution="Issue resolved at Level 4 by task rewrite",
                needs_human_input=False,
            )

        result = await self._level_5_human_input(task_id=task_id, error_detail=error_detail)
        self._record_event(
            task_id=task_id,
            agent_id=agent_id,
            level=EscalationLevel.HUMAN_INPUT,
            error_signature=error_signature,
            error_detail=error_detail,
            loop_count=loop_count,
            resolved=False,
            resolution="Task requires human guidance",
        )
        return result

    async def _level_1_self_retry(self, task_id: str, agent_id: str) -> bool:
        """Stub self-retry: always fails until retries exhausted for task."""
        attempts = self._self_retry_attempts.get(task_id, 0)
        if attempts >= self.max_self_retries:
            return False
        self._self_retry_attempts[task_id] = attempts + 1
        logger.warning(
            "Escalation level attempt: task_id=%s level=%d outcome=failed agent_id=%s timestamp=%s",
            task_id,
            EscalationLevel.SELF_RETRY,
            agent_id,
            datetime.now(UTC).isoformat(),
        )
        return False

    async def _level_2_peer_assist(self, task_id: str, agent_id: str) -> bool:
        """Stub peer assist: unresolved in Phase 3."""
        logger.warning(
            "Escalation level attempt: task_id=%s level=%d outcome=failed agent_id=%s timestamp=%s",
            task_id,
            EscalationLevel.PEER_ASSIST,
            agent_id,
            datetime.now(UTC).isoformat(),
        )
        return False

    async def _level_3_architect_review(self, task_id: str, task_specification: str) -> bool:
        """Stub architect review: unresolved in Phase 3."""
        _ = task_specification
        logger.warning(
            "Escalation level attempt: task_id=%s level=%d outcome=failed timestamp=%s",
            task_id,
            EscalationLevel.ARCHITECT_REVIEW,
            datetime.now(UTC).isoformat(),
        )
        return False

    async def _level_4_task_rewrite(self, task_id: str, task_specification: str) -> bool:
        """Stub task rewrite: unresolved in Phase 3."""
        _ = task_specification
        logger.warning(
            "Escalation level attempt: task_id=%s level=%d outcome=failed timestamp=%s",
            task_id,
            EscalationLevel.TASK_REWRITE,
            datetime.now(UTC).isoformat(),
        )
        return False

    async def _level_5_human_input(self, task_id: str, error_detail: str) -> EscalationResult:
        """Return level-5 result instructing user intervention."""
        logger.error(
            "Escalation level attempt: task_id=%s level=%d outcome=needs_human_input timestamp=%s",
            task_id,
            EscalationLevel.HUMAN_INPUT,
            datetime.now(UTC).isoformat(),
        )
        return EscalationResult(
            level_reached=EscalationLevel.HUMAN_INPUT,
            resolved=False,
            resolution="All automated recovery attempts failed",
            needs_human_input=True,
            human_message=(
                "The task has failed after all automated recovery attempts. "
                f"The core issue is: {error_detail}. Please review the task "
                "specification or provide guidance."
            ),
        )

    def get_events(self, task_id: str) -> list[EscalationEvent]:
        """Return all escalation events for a task ordered by timestamp."""
        return sorted(
            [event for event in self._events if event.task_id == task_id],
            key=lambda item: item.timestamp,
        )

    def get_current_level(self, task_id: str) -> EscalationLevel | None:
        """Return highest escalation level reached for task, or None."""
        task_events = self.get_events(task_id)
        if not task_events:
            return None
        return max(event.level for event in task_events)

    def _record_event(
        self,
        *,
        task_id: str,
        agent_id: str,
        level: EscalationLevel,
        error_signature: str,
        error_detail: str,
        loop_count: int,
        resolved: bool,
        resolution: str,
    ) -> None:
        event = EscalationEvent(
            task_id=task_id,
            agent_id=agent_id,
            level=level,
            error_signature=error_signature,
            error_detail=error_detail,
            loop_count=loop_count,
            timestamp=datetime.now(UTC),
            resolved=resolved,
            resolution=resolution,
        )
        self._events.append(event)
        logger.warning(
            "Escalation event: task_id=%s level=%d outcome=%s timestamp=%s",
            task_id,
            level,
            "resolved" if resolved else "failed",
            event.timestamp.isoformat(),
        )
