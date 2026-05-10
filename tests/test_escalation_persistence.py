"""Tests for EscalationPersistence."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.escalation.persistence import EscalationPersistence
from forgeai.escalation.schemas import EscalationEvent, EscalationLevel
from datetime import UTC, datetime


def _event(
    task_id: str,
    *,
    level: EscalationLevel = EscalationLevel.SELF_RETRY,
    attempted: datetime | None = None,
) -> EscalationEvent:
    ts = attempted or datetime.now(UTC)
    return EscalationEvent(
        id=str(uuid.uuid4()),
        task_id=task_id,
        agent_id="agent_a",
        level=level,
        error_signature="sig",
        error_detail="detail",
        loop_count=1,
        timestamp=ts,
        resolved=False,
        resolution="",
    )


@pytest.mark.asyncio
async def test_save_event_writes_to_database(db_session: AsyncSession) -> None:
    p = EscalationPersistence(db_session)
    tid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ev = _event(tid)
    await p.save_event(ev)
    await db_session.commit()
    got = await p.get_events(tid)
    assert len(got) == 1
    assert got[0].level == EscalationLevel.SELF_RETRY


@pytest.mark.asyncio
async def test_get_events_ordered_chronologically(db_session: AsyncSession) -> None:
    p = EscalationPersistence(db_session)
    tid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)
    e0 = _event(tid, attempted=t0)
    e1 = _event(tid, level=EscalationLevel.PEER_ASSIST, attempted=t1)
    await p.save_event(e0)
    await p.save_event(e1)
    await db_session.commit()
    rows = await p.get_events(tid)
    assert [r.timestamp for r in rows] == sorted([r.timestamp for r in rows])


@pytest.mark.asyncio
async def test_get_events_empty_unknown_task(db_session: AsyncSession) -> None:
    p = EscalationPersistence(db_session)
    assert await p.get_events("99999999-9999-9999-9999-999999999999") == []


@pytest.mark.asyncio
async def test_mark_resolved(db_session: AsyncSession) -> None:
    p = EscalationPersistence(db_session)
    tid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    ev = _event(tid)
    await p.save_event(ev)
    await db_session.commit()
    loaded = await p.get_events(tid)
    eid = loaded[0].id
    await p.mark_resolved(eid, "fixed upstream")
    await db_session.commit()
    again = await p.get_events(tid)
    assert again[0].resolved is True
    assert again[0].resolution == "fixed upstream"


@pytest.mark.asyncio
async def test_events_scoped_by_task_id(db_session: AsyncSession) -> None:
    p = EscalationPersistence(db_session)
    t1 = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    t2 = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    await p.save_event(_event(t1))
    await p.save_event(_event(t2))
    await db_session.commit()
    assert len(await p.get_events(t1)) == 1
    assert len(await p.get_events(t2)) == 1
