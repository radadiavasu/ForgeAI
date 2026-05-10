"""Single entry point for Anthropic API calls (Phase 5)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from anthropic import AsyncAnthropic, RateLimitError

from forgeai.exceptions import LLMRateLimitError
from forgeai.llm.model_router import ModelRouter
from forgeai.llm.schemas import LLMResponse

logger = logging.getLogger(__name__)

_MAX_RATE_LIMIT_RETRIES = 3

# Pricing USD per 1M tokens (Phase 5 spec)
_HAIKU_IN = 0.80
_HAIKU_OUT = 4.00
_SONNET_IN = 3.00
_SONNET_OUT = 15.00
_OPUS_IN = 15.00
_OPUS_OUT = 75.00


class LLMClient:
    """All completion calls go through this class; enforces ``ModelRouter``."""

    def __init__(self, api_key: str, model_router: ModelRouter) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self.router = model_router

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Return estimated cost in USD from token usage and model id."""
        m = model.lower()
        if "haiku" in m:
            return (input_tokens * _HAIKU_IN + output_tokens * _HAIKU_OUT) / 1_000_000
        if "opus" in m:
            return (input_tokens * _OPUS_IN + output_tokens * _OPUS_OUT) / 1_000_000
        return (input_tokens * _SONNET_IN + output_tokens * _SONNET_OUT) / 1_000_000

    def _collect_text_and_tools(self, content: list[Any]) -> tuple[str, list[Any]]:
        """Build text and tool call records from API content blocks."""
        text_parts: list[str] = []
        tool_calls: list[Any] = []
        for block in content:
            btype = getattr(block, "type", None) or (
                block.get("type") if isinstance(block, dict) else None
            )
            if btype == "text":
                t = getattr(block, "text", None)
                if t is None and isinstance(block, dict):
                    t = block.get("text", "")
                if isinstance(t, str):
                    text_parts.append(t)
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "type": "tool_use",
                        "id": getattr(block, "id", None) or (block or {}).get("id"),
                        "name": getattr(block, "name", None) or (block or {}).get("name"),
                        "input": getattr(block, "input", None) or (block or {}).get("input"),
                    }
                )
            else:
                # Non-text (e.g. thinking): skip text but log at debug
                logger.debug("Skipping non-text assistant block type=%s", btype)
        return "".join(text_parts), tool_calls

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        complexity: str,
        loop_count: int = 0,
        max_tokens: int = 1000,
        tools: list[Any] | None = None,
    ) -> LLMResponse:
        """Run one routed completion with retries on rate limits."""
        model = self.router.route(complexity, loop_count)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }
        if tools:
            kwargs["tools"] = tools

        last_exc: Exception | None = None
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            try:
                msg = await self._client.messages.create(**kwargs)
                usage = msg.usage
                in_tok = int(getattr(usage, "input_tokens", 0) or 0)
                out_tok = int(getattr(usage, "output_tokens", 0) or 0)
                text, tool_calls = self._collect_text_and_tools(list(msg.content))
                cost = self._estimate_cost(model, in_tok, out_tok)
                logger.info(
                    "LLM complete model=%s in_tokens=%s out_tokens=%s est_cost_usd=%.6f",
                    model,
                    in_tok,
                    out_tok,
                    cost,
                )
                return LLMResponse(
                    content=text,
                    model_used=model,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    estimated_cost_usd=cost,
                    tool_calls=tool_calls,
                )
            except RateLimitError as e:
                last_exc = e
                if attempt >= _MAX_RATE_LIMIT_RETRIES:
                    raise LLMRateLimitError(
                        "Anthropic rate limit persists after retries"
                    ) from e
                delay = 2**attempt
                logger.warning(
                    "Rate limited (429); retry %s/%s after %ss",
                    attempt + 1,
                    _MAX_RATE_LIMIT_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)

        raise LLMRateLimitError("Anthropic rate limit persists after retries") from last_exc
