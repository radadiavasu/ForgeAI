"""Unit tests for EscalationLadder routing and guardrails."""

from __future__ import annotations

from forgeai.escalation import EscalationLadder, EscalationLevel, LoopCounter
from forgeai.exceptions import AlreadyEscalatedError


async def _escalate_once(
    ladder: EscalationLadder,
    *,
    task_id: str = "task-1",
    signature: str = "test_failure:assertion_error",
):
    return await ladder.escalate(
        task_id=task_id,
        agent_id="lead_agent_1",
        error_signature=signature,
        error_detail="assertion failures",
        task_specification="Build Payment API",
    )


import pytest


@pytest.mark.asyncio
async def test_level_one_attempted_first() -> None:
    ladder = EscalationLadder(loop_counter=LoopCounter(), max_self_retries=2)
    await _escalate_once(ladder)
    events = ladder.get_events("task-1")
    assert events[0].level == EscalationLevel.SELF_RETRY


@pytest.mark.asyncio
async def test_level_one_attempted_max_two_times() -> None:
    ladder = EscalationLadder(loop_counter=LoopCounter(), max_self_retries=2)
    await _escalate_once(ladder)
    assert ladder._self_retry_attempts["task-1"] == 1
    assert ladder.max_self_retries == 2


@pytest.mark.asyncio
async def test_level_two_reached_after_level_one_exhausted() -> None:
    ladder = EscalationLadder(loop_counter=LoopCounter(), max_self_retries=2)
    await _escalate_once(ladder)
    events = ladder.get_events("task-1")
    assert any(evt.level == EscalationLevel.PEER_ASSIST for evt in events)


@pytest.mark.asyncio
async def test_all_five_levels_reached_when_none_resolve() -> None:
    ladder = EscalationLadder(loop_counter=LoopCounter(), max_self_retries=2)
    result = await _escalate_once(ladder)
    levels = {evt.level for evt in ladder.get_events("task-1")}
    assert levels == {
        EscalationLevel.SELF_RETRY,
        EscalationLevel.PEER_ASSIST,
        EscalationLevel.ARCHITECT_REVIEW,
        EscalationLevel.TASK_REWRITE,
        EscalationLevel.HUMAN_INPUT,
    }
    assert result.level_reached == EscalationLevel.HUMAN_INPUT


@pytest.mark.asyncio
async def test_level_five_requires_human_input() -> None:
    ladder = EscalationLadder(loop_counter=LoopCounter(), max_self_retries=2)
    result = await _escalate_once(ladder)
    assert result.needs_human_input is True


@pytest.mark.asyncio
async def test_level_five_human_message_non_empty() -> None:
    ladder = EscalationLadder(loop_counter=LoopCounter(), max_self_retries=2)
    result = await _escalate_once(ladder)
    assert bool(result.human_message.strip())


@pytest.mark.asyncio
async def test_loop_threshold_skips_level_one() -> None:
    counter = LoopCounter()
    counter.increment("task-1", "test_failure:assertion_error")
    counter.increment("task-1", "test_failure:assertion_error")
    ladder = EscalationLadder(loop_counter=counter, max_self_retries=2)
    await _escalate_once(ladder)
    events = ladder.get_events("task-1")
    assert events[0].level == EscalationLevel.PEER_ASSIST


@pytest.mark.asyncio
async def test_get_events_chronological() -> None:
    ladder = EscalationLadder(loop_counter=LoopCounter(), max_self_retries=2)
    await _escalate_once(ladder)
    events = ladder.get_events("task-1")
    assert events == sorted(events, key=lambda item: item.timestamp)


@pytest.mark.asyncio
async def test_get_current_level_returns_highest() -> None:
    ladder = EscalationLadder(loop_counter=LoopCounter(), max_self_retries=2)
    await _escalate_once(ladder)
    assert ladder.get_current_level("task-1") == EscalationLevel.HUMAN_INPUT


@pytest.mark.asyncio
async def test_no_retry_after_level_five() -> None:
    ladder = EscalationLadder(loop_counter=LoopCounter(), max_self_retries=2)
    await _escalate_once(ladder)
    with pytest.raises(AlreadyEscalatedError):
        await _escalate_once(ladder)
