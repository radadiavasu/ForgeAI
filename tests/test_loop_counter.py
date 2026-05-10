"""Unit tests for LoopCounter behavior (Redis-backed)."""

import pytest

from forgeai.escalation.loop_counter import LoopCounter


@pytest.mark.asyncio
async def test_increment_returns_correct_count() -> None:
    counter = LoopCounter()
    assert await counter.increment("task-1", "sandbox_timeout") == 1
    assert await counter.increment("task-1", "sandbox_timeout") == 2


@pytest.mark.asyncio
async def test_get_returns_zero_for_unseen_combination() -> None:
    counter = LoopCounter()
    assert await counter.get("unknown-task", "schema_violation") == 0


@pytest.mark.asyncio
async def test_should_escalate_false_below_threshold() -> None:
    counter = LoopCounter()
    await counter.increment("task-1", "output_missing")
    await counter.increment("task-1", "output_missing")
    assert await counter.should_escalate("task-1", "output_missing") is False


@pytest.mark.asyncio
async def test_should_escalate_true_at_three() -> None:
    counter = LoopCounter()
    for _ in range(3):
        await counter.increment("task-1", "output_missing")
    assert await counter.should_escalate("task-1", "output_missing") is True


@pytest.mark.asyncio
async def test_reset_clears_all_task_counters() -> None:
    counter = LoopCounter()
    await counter.increment("task-1", "output_missing")
    await counter.increment("task-1", "sandbox_timeout")
    await counter.reset("task-1")
    assert await counter.get("task-1", "output_missing") == 0
    assert await counter.get("task-1", "sandbox_timeout") == 0


@pytest.mark.asyncio
async def test_reset_does_not_affect_other_tasks() -> None:
    counter = LoopCounter()
    await counter.increment("task-1", "output_missing")
    await counter.increment("task-2", "output_missing")
    await counter.reset("task-1")
    assert await counter.get("task-1", "output_missing") == 0
    assert await counter.get("task-2", "output_missing") == 1


@pytest.mark.asyncio
async def test_signatures_tracked_independently_per_task() -> None:
    counter = LoopCounter()
    await counter.increment("task-1", "output_missing")
    await counter.increment("task-1", "sandbox_timeout")
    await counter.increment("task-1", "sandbox_timeout")
    assert await counter.get("task-1", "output_missing") == 1
    assert await counter.get("task-1", "sandbox_timeout") == 2
