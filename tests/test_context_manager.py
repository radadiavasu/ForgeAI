"""Context_Window_Manager tests — mocked LLM."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from forgeai.exceptions import ContextWindowExceededError
from forgeai.intelligence.context_manager import ContextWindowManager
from forgeai.llm.schemas import LLMResponse
from forgeai.memory.task_memory import TaskMemory


@pytest.fixture
def task_memory() -> TaskMemory:
    return TaskMemory("redis://localhost:6379/0", ttl_seconds=3600)


@pytest.fixture
def mock_llm() -> AsyncMock:
    return AsyncMock()


def test_estimate_tokens_reasonable() -> None:
    mgr = ContextWindowManager(AsyncMock(), TaskMemory("redis://localhost:6379/0"))
    assert mgr.estimate_tokens("a" * 400) == 100


@pytest.mark.asyncio
async def test_check_and_reduce_unchanged_below_threshold(
    mock_llm: AsyncMock,
    task_memory: TaskMemory,
) -> None:
    mgr = ContextWindowManager(mock_llm, task_memory)
    small = "hello " * 10
    result = await mgr.check_and_reduce(
        small, "claude-sonnet-4-6", "task-1", "agent-1"
    )
    assert result.reduction_applied is False
    assert result.reduced_context == small


@pytest.mark.asyncio
async def test_check_and_reduce_applies_strategies(
    mock_llm: AsyncMock,
    task_memory: TaskMemory,
) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content="digest summary",
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    await task_memory.set("task-big", "note", "important defect detail")
    mgr = ContextWindowManager(mock_llm, task_memory)
    huge = "x" * 700_000
    result = await mgr.check_and_reduce(
        huge,
        "claude-sonnet-4-6",
        "task-big",
        "agent-1",
        master_doc_section="Auth section only",
    )
    assert result.reduction_applied is True
    assert result.strategies_used


@pytest.mark.asyncio
async def test_reduction_log_records_events(
    mock_llm: AsyncMock,
    task_memory: TaskMemory,
) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content="digest",
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    await task_memory.set("t-log", "k", "v")
    mgr = ContextWindowManager(mock_llm, task_memory)
    await mgr.check_and_reduce("y" * 700_000, "claude-sonnet-4-6", "t-log", "a1")
    assert len(mgr.get_reduction_log()) >= 1


@pytest.mark.asyncio
async def test_context_window_exceeded_when_still_over_limit(
    mock_llm: AsyncMock,
    task_memory: TaskMemory,
) -> None:
    mgr = ContextWindowManager(mock_llm, task_memory)
    mgr.MODEL_TOKEN_LIMITS["claude-sonnet-4-6"] = 100
    huge = "z" * 10_000
    mgr.WARNING_THRESHOLD = 0.5
    with pytest.raises(ContextWindowExceededError):
        await mgr.check_and_reduce(huge, "claude-sonnet-4-6", "t", "a")


@pytest.mark.asyncio
async def test_summarise_task_memory_uses_low_complexity(
    mock_llm: AsyncMock,
    task_memory: TaskMemory,
) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content="summary",
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    await task_memory.set("t-sum", "defect_report", "failed tests")
    mgr = ContextWindowManager(mock_llm, task_memory)
    text = await mgr._summarise_task_memory("t-sum")
    assert text == "summary"
    assert mock_llm.complete.await_args.kwargs.get("complexity") == "LOW"
