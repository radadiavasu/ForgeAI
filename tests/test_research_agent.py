"""ResearchAgent tests — LLMClient fully mocked."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from forgeai.agents.research_agent import WEB_SEARCH_TOOL, ResearchAgent
from forgeai.llm.schemas import LLMResponse


def _sample_research_json() -> str:
    payload = {
        "domain_summary": "Restaurant booking system",
        "technology_options": [
            {
                "name": "FastAPI",
                "pros": ["async"],
                "cons": ["smaller"],
                "suitable": True,
            },
            {
                "name": "Django",
                "pros": ["batteries"],
                "cons": ["heavier"],
                "suitable": False,
            },
        ],
        "recommended_stack": {
            "language": "Python",
            "framework": "FastAPI",
            "database": "PostgreSQL",
            "testing_framework": "pytest",
            "rationale": "Fits stack constraints",
            "rejected_alternatives": ["Django"],
        },
        "constraints_respected": ["Python"],
        "research_sources": ["https://example.com/doc"],
    }
    return json.dumps(payload)


@pytest.mark.asyncio
async def test_research_calls_low_tier_first(db_session) -> None:
    """Research uses LOW (Haiku tier) first for cost; router still applies."""
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_sample_research_json(),
        model_used="claude-sonnet-4-6",
        input_tokens=500,
        output_tokens=200,
        estimated_cost_usd=0.0045,
    )
    mock_memory = MagicMock()
    mock_memory.retrieve_lessons = AsyncMock(return_value=[])

    agent = ResearchAgent("research_1", db_session, mock_llm, mock_memory)
    await agent.research("Build a booking system", {})

    kwargs = mock_llm.complete.await_args.kwargs
    assert kwargs["complexity"] == "LOW"


@pytest.mark.asyncio
async def test_research_falls_back_to_medium_when_low_output_invalid(db_session) -> None:
    """If LOW-tier JSON/schema fails, retry once with MEDIUM."""
    mock_llm = AsyncMock()
    mock_llm.complete.side_effect = [
        LLMResponse(
            content="{ not valid json",
            model_used="claude-haiku-4-5-20251001",
            input_tokens=100,
            output_tokens=50,
            estimated_cost_usd=0.0,
        ),
        LLMResponse(
            content=_sample_research_json(),
            model_used="claude-sonnet-4-6",
            input_tokens=500,
            output_tokens=200,
            estimated_cost_usd=0.0045,
        ),
    ]
    mock_memory = MagicMock()
    mock_memory.retrieve_lessons = AsyncMock(return_value=[])

    agent = ResearchAgent("research_1", db_session, mock_llm, mock_memory)
    await agent.research("Build a booking system", {})

    assert mock_llm.complete.await_count == 2
    assert mock_llm.complete.await_args_list[0].kwargs["complexity"] == "LOW"
    assert mock_llm.complete.await_args_list[1].kwargs["complexity"] == "MEDIUM"


@pytest.mark.asyncio
async def test_research_queries_memory_before_llm(db_session) -> None:
    order: list[str] = []

    async def mem_track(*_a, **_kw):
        order.append("memory")
        return []

    mock_memory = MagicMock()
    mock_memory.retrieve_lessons = AsyncMock(side_effect=mem_track)

    mock_llm = AsyncMock()

    async def llm_track(*_a, **_kw):
        order.append("llm")
        return LLMResponse(
            content=_sample_research_json(),
            model_used="claude-sonnet-4-6",
            input_tokens=10,
            output_tokens=10,
            estimated_cost_usd=0.0,
        )

    mock_llm.complete = AsyncMock(side_effect=llm_track)

    agent = ResearchAgent("research_1", db_session, mock_llm, mock_memory)
    await agent.research("Brief", {})

    assert order == ["memory", "llm"]


@pytest.mark.asyncio
async def test_research_passes_web_search_tool(db_session) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_sample_research_json(),
        model_used="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    mock_memory = MagicMock()
    mock_memory.retrieve_lessons = AsyncMock(return_value=[])

    agent = ResearchAgent("research_1", db_session, mock_llm, mock_memory)
    await agent.research("x", {})

    tools = mock_llm.complete.await_args.kwargs.get("tools") or []
    assert tools == [WEB_SEARCH_TOOL]


@pytest.mark.asyncio
async def test_research_returns_valid_output(db_session) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_sample_research_json(),
        model_used="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    mock_memory = MagicMock()
    mock_memory.retrieve_lessons = AsyncMock(return_value=[])

    agent = ResearchAgent("research_1", db_session, mock_llm, mock_memory)
    out = await agent.research("Brief", {})
    assert out.domain_summary != ""
    assert len(out.technology_options) >= 1
    assert out.recommended_stack.language


@pytest.mark.asyncio
async def test_research_output_has_options_and_stack(db_session) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_sample_research_json(),
        model_used="claude-sonnet-4-6",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    mock_memory = MagicMock()
    mock_memory.retrieve_lessons = AsyncMock(return_value=[])

    agent = ResearchAgent("research_1", db_session, mock_llm, mock_memory)
    out = await agent.research("Brief", {})
    assert len(out.technology_options) >= 1
    assert out.recommended_stack.framework
