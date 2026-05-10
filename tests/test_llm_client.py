"""Tests for LLMClient — mocked Anthropic SDK only."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic import RateLimitError

from forgeai.exceptions import LLMRateLimitError
from forgeai.llm.client import LLMClient
from forgeai.llm.model_router import ModelRouter
from forgeai.llm.schemas import ModelPool, TierPool


@pytest.fixture
def pool() -> ModelPool:
    return ModelPool(
        low=TierPool(default="low-def", escalated="low-esc"),
        medium=TierPool(default="medium-def", escalated="medium-esc"),
        high=TierPool(default="high-def", escalated="high-esc"),
    )


def _text_block(text: str) -> MagicMock:
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _429_response() -> MagicMock:
    r = MagicMock()
    r.status_code = 429
    return r


@pytest.mark.asyncio
async def test_complete_calls_client_with_router_model(pool: ModelPool) -> None:
    router = ModelRouter(pool)
    client = LLMClient("sk-test", router)
    mock_msg = MagicMock()
    mock_msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    mock_msg.content = [_text_block("hi")]
    create = AsyncMock(return_value=mock_msg)
    client._client.messages.create = create

    await client.complete("sys", "user", "MEDIUM", loop_count=0)

    create.assert_awaited_once()
    assert create.await_args.kwargs["model"] == "medium-def"


@pytest.mark.asyncio
async def test_complete_not_hardcoded_model_uses_router(pool: ModelPool) -> None:
    router = ModelRouter(pool)
    client = LLMClient("sk-test", router)
    mock_msg = MagicMock()
    mock_msg.usage = MagicMock(input_tokens=10, output_tokens=10)
    mock_msg.content = [_text_block("x")]
    client._client.messages.create = AsyncMock(return_value=mock_msg)

    await client.complete("s", "u", "HIGH", loop_count=0)
    assert client._client.messages.create.await_args.kwargs["model"] == "high-def"


@pytest.mark.asyncio
async def test_loop_count_2_escalated_model(pool: ModelPool) -> None:
    router = ModelRouter(pool)
    client = LLMClient("sk-test", router)
    mock_msg = MagicMock()
    mock_msg.usage = MagicMock(input_tokens=1, output_tokens=1)
    mock_msg.content = [_text_block("y")]
    client._client.messages.create = AsyncMock(return_value=mock_msg)

    await client.complete("s", "u", "MEDIUM", loop_count=2)
    assert client._client.messages.create.await_args.kwargs["model"] == "medium-esc"


@pytest.mark.asyncio
async def test_rate_limit_retries_then_success(pool: ModelPool) -> None:
    router = ModelRouter(pool)
    client = LLMClient("sk-test", router)
    ok = MagicMock()
    ok.usage = MagicMock(input_tokens=5, output_tokens=5)
    ok.content = [_text_block("ok")]
    create = AsyncMock(
        side_effect=[
            RateLimitError("429", response=_429_response(), body=None),
            ok,
        ],
    )
    client._client.messages.create = create

    with patch("forgeai.llm.client.asyncio.sleep", new_callable=AsyncMock):
        resp = await client.complete("s", "u", "LOW", 0)

    assert resp.content == "ok"
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_rate_limit_max_retries_raises(pool: ModelPool) -> None:
    router = ModelRouter(pool)
    client = LLMClient("sk-test", router)
    client._client.messages.create = AsyncMock(
        side_effect=RateLimitError("429", response=_429_response(), body=None),
    )

    with patch("forgeai.llm.client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(LLMRateLimitError):
            await client.complete("s", "u", "LOW", 0)


def test_cost_estimation_haiku_sonnet_opus(pool: ModelPool) -> None:
    router = ModelRouter(pool)
    client = LLMClient("sk", router)
    h = client._estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert abs(h - (0.80 + 4.00)) < 0.001
    s = client._estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert abs(s - (3.00 + 15.00)) < 0.001
    o = client._estimate_cost("claude-opus-4-6", 1_000_000, 1_000_000)
    assert abs(o - (15.00 + 75.00)) < 0.001


@pytest.mark.asyncio
async def test_llm_response_populated(pool: ModelPool) -> None:
    router = ModelRouter(pool)
    client = LLMClient("sk-test", router)
    mock_msg = MagicMock()
    mock_msg.usage = MagicMock(input_tokens=100, output_tokens=200)
    mock_msg.content = [_text_block("body")]
    client._client.messages.create = AsyncMock(return_value=mock_msg)

    resp = await client.complete("s", "u", "LOW", 0)
    assert resp.content == "body"
    assert resp.input_tokens == 100
    assert resp.output_tokens == 200
    assert resp.model_used == "low-def"
    assert resp.estimated_cost_usd > 0
