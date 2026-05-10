"""PostgreSQL persistence for escalation events."""

from __future__ import annotations

import uuid
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.escalation.schemas import EscalationEvent, EscalationLevel
from forgeai.models.escalation import EscalationEventModel


class EscalationPersistence:
    """Stores and queries escalation events."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    async def save_event(self, event: EscalationEvent) -> None:
        row_id = uuid.UUID(event.id) if (event.id and event.id.strip()) else uuid.uuid4()
        tid = uuid.UUID(event.task_id)
        row = EscalationEventModel(
            id=row_id,
            task_id=tid,
            agent_id=event.agent_id,
            level=int(event.level),
            error_signature=event.error_signature,
            error_detail=event.error_detail,
            loop_count=event.loop_count,
            attempted_at=event.timestamp,
            resolved=event.resolved,
            resolution=event.resolution or None,
            needs_human_input=event.needs_human_input,
            human_message=event.human_message or None,
        )
        self.db.add(row)
        await self.db.flush()

    async def get_events(self, task_id: str) -> list[EscalationEvent]:
        tid = uuid.UUID(task_id)
        result = await self.db.execute(
            select(EscalationEventModel)
            .where(EscalationEventModel.task_id == tid)
            .order_by(EscalationEventModel.attempted_at.asc())
        )
        rows = list(result.scalars().all())
        out: list[EscalationEvent] = []
        for r in rows:
            out.append(
                EscalationEvent(
                    id=str(r.id),
                    task_id=str(r.task_id),
                    agent_id=r.agent_id,
                    level=EscalationLevel(r.level),
                    error_signature=r.error_signature,
                    error_detail=r.error_detail,
                    loop_count=r.loop_count,
                    timestamp=r.attempted_at,
                    resolved=r.resolved,
                    resolution=r.resolution or "",
                    needs_human_input=r.needs_human_input,
                    human_message=r.human_message or "",
                )
            )
        return out

    async def mark_resolved(self, event_id: str, resolution: str) -> None:
        eid = uuid.UUID(event_id)
        await self.db.execute(
            update(EscalationEventModel)
            .where(EscalationEventModel.id == eid)
            .values(resolved=True, resolution=resolution)
        )
        await self.db.flush()
