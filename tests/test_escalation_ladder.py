"""Unit tests for EscalationLadder routing and guardrails."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.escalation import (
    EscalationLadder,
    EscalationLevel,
    EscalationPersistence,
    LoopCounter,
)
from forgeai.exceptions import AlreadyEscalatedError

TASK_UUID = "11111111-1111-1111-1111-111111111111"


async def _escalate_once(
    ladder: EscalationLadder,
    *,
    task_id: str = TASK_UUID,
    signature: str = "test_failure:assertion_error",
):
    return await ladder.escalate(
        task_id=task_id,
        agent_id="lead_agent_1",
        error_signature=signature,
        error_detail="assertion failures",
        task_specification="Build Payment API",
    )


@pytest.mark.asyncio
async def test_level_one_attempted_first(db_session: AsyncSession) -> None:
    ladder = EscalationLadder(
        loop_counter=LoopCounter(),
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )
    await _escalate_once(ladder)
    events = await ladder.get_events(TASK_UUID)
    assert events[0].level == EscalationLevel.SELF_RETRY


@pytest.mark.asyncio
async def test_level_one_attempted_max_two_times(db_session: AsyncSession) -> None:
    ladder = EscalationLadder(
        loop_counter=LoopCounter(),
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )
    await _escalate_once(ladder)
    assert ladder._self_retry_attempts[TASK_UUID] == 1
    assert ladder.max_self_retries == 2


@pytest.mark.asyncio
async def test_level_two_reached_after_level_one_exhausted(db_session: AsyncSession) -> None:
    ladder = EscalationLadder(
        loop_counter=LoopCounter(),
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )
    await _escalate_once(ladder)
    events = await ladder.get_events(TASK_UUID)
    assert any(evt.level == EscalationLevel.PEER_ASSIST for evt in events)


@pytest.mark.asyncio
async def test_all_five_levels_reached_when_none_resolve(db_session: AsyncSession) -> None:
    ladder = EscalationLadder(
        loop_counter=LoopCounter(),
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )
    result = await _escalate_once(ladder)
    events = await ladder.get_events(TASK_UUID)
    levels = {evt.level for evt in events}
    assert levels == {
        EscalationLevel.SELF_RETRY,
        EscalationLevel.PEER_ASSIST,
        EscalationLevel.ARCHITECT_REVIEW,
        EscalationLevel.TASK_REWRITE,
        EscalationLevel.HUMAN_INPUT,
    }
    assert result.level_reached == EscalationLevel.HUMAN_INPUT


@pytest.mark.asyncio
async def test_level_five_requires_human_input(db_session: AsyncSession) -> None:
    ladder = EscalationLadder(
        loop_counter=LoopCounter(),
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )
    result = await _escalate_once(ladder)
    assert result.needs_human_input is True


@pytest.mark.asyncio
async def test_level_five_human_message_non_empty(db_session: AsyncSession) -> None:
    ladder = EscalationLadder(
        loop_counter=LoopCounter(),
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )
    result = await _escalate_once(ladder)
    assert bool(result.human_message.strip())


@pytest.mark.asyncio
async def test_loop_threshold_skips_level_one(db_session: AsyncSession) -> None:
    counter = LoopCounter()
    await counter.increment(TASK_UUID, "test_failure:assertion_error")
    await counter.increment(TASK_UUID, "test_failure:assertion_error")
    ladder = EscalationLadder(
        loop_counter=counter,
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )
    await _escalate_once(ladder)
    events = await ladder.get_events(TASK_UUID)
    assert events[0].level == EscalationLevel.PEER_ASSIST


@pytest.mark.asyncio
async def test_get_events_chronological(db_session: AsyncSession) -> None:
    ladder = EscalationLadder(
        loop_counter=LoopCounter(),
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )
    await _escalate_once(ladder)
    events = await ladder.get_events(TASK_UUID)
    assert events == sorted(events, key=lambda item: item.timestamp)


@pytest.mark.asyncio
async def test_get_current_level_returns_highest(db_session: AsyncSession) -> None:
    ladder = EscalationLadder(
        loop_counter=LoopCounter(),
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )
    await _escalate_once(ladder)
    assert await ladder.get_current_level(TASK_UUID) == EscalationLevel.HUMAN_INPUT


@pytest.mark.asyncio
async def test_no_retry_after_level_five(db_session: AsyncSession) -> None:
    ladder = EscalationLadder(
        loop_counter=LoopCounter(),
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )
    await _escalate_once(ladder)
    with pytest.raises(AlreadyEscalatedError):
        await _escalate_once(ladder)
