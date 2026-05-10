"""ArchitectAgent tests — LLMClient fully mocked."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from forgeai.agents.architect_agent import ArchitectAgent
from forgeai.llm.schemas import LLMResponse, ResearchOutput, TechStack, TechnologyOption


def _research() -> ResearchOutput:
    return ResearchOutput(
        domain_summary="Domain",
        technology_options=[
            TechnologyOption(name="A", pros=[], cons=[], suitable=True),
            TechnologyOption(name="B", pros=[], cons=[], suitable=False),
        ],
        recommended_stack=TechStack(
            language="Python",
            framework="FastAPI",
            database="PostgreSQL",
            testing_framework="pytest",
            rationale="ok",
            rejected_alternatives=[],
        ),
        constraints_respected=[],
        research_sources=[],
    )


def _master_json() -> str:
    ts = {
        "language": "Python",
        "framework": "FastAPI",
        "database": "PostgreSQL",
        "testing_framework": "pytest",
        "rationale": "ok",
        "rejected_alternatives": [],
    }
    payload = {
        "version": "1.0",
        "project_name": "Demo",
        "project_summary": "Summary",
        "components": [
            {
                "name": "API",
                "responsibility": "HTTP",
                "dependencies": [],
                "acceptance_criteria": ["works"],
            }
        ],
        "data_models": [
            {
                "name": "MenuItem",
                "fields": [
                    {
                        "name": "id",
                        "type": "uuid",
                        "required": True,
                        "description": "pk",
                    }
                ],
            }
        ],
        "api_surfaces": [
            {
                "endpoint": "/menu",
                "method": "GET",
                "request_schema": {},
                "response_schema": {},
                "description": "list",
            }
        ],
        "tech_stack": ts,
        "constraints": [],
        "created_at": datetime.now(UTC).isoformat(),
    }
    return json.dumps(payload)


def _tech_stack_doc_json() -> str:
    payload = {
        "language": "Python",
        "framework": "FastAPI",
        "database": "PostgreSQL",
        "testing_framework": "pytest",
        "libraries": ["uvicorn"],
        "rationale": "fits",
        "rejected_alternatives": [],
        "version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
    }
    return json.dumps(payload)


@pytest.mark.asyncio
async def test_master_document_high_complexity(db_session) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_master_json(),
        model_used="claude-opus-4-6",
        input_tokens=100,
        output_tokens=100,
        estimated_cost_usd=0.01,
    )
    mock_memory = MagicMock()
    mock_memory.retrieve_lessons = AsyncMock(return_value=[])

    agent = ArchitectAgent("arch_1", db_session, mock_llm, mock_memory)
    await agent.produce_master_document("Brief", _research(), {})

    assert mock_llm.complete.await_args.kwargs["complexity"] == "HIGH"


@pytest.mark.asyncio
async def test_master_queries_memory_before_llm(db_session) -> None:
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
            content=_master_json(),
            model_used="x",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        )

    mock_llm.complete = AsyncMock(side_effect=llm_track)

    agent = ArchitectAgent("arch_1", db_session, mock_llm, mock_memory)
    await agent.produce_master_document("Brief", _research(), {})

    assert order == ["memory", "llm"]


@pytest.mark.asyncio
async def test_master_document_structure(db_session) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_master_json(),
        model_used="x",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    mock_memory = MagicMock()
    mock_memory.retrieve_lessons = AsyncMock(return_value=[])

    agent = ArchitectAgent("arch_1", db_session, mock_llm, mock_memory)
    md = await agent.produce_master_document("Brief", _research(), {})
    assert len(md.components) >= 1
    assert len(md.api_surfaces) >= 1
    assert len(md.data_models) >= 1


@pytest.mark.asyncio
async def test_tech_stack_document(db_session) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_tech_stack_doc_json(),
        model_used="x",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    mock_memory = MagicMock()
    mock_memory.retrieve_lessons = AsyncMock(return_value=[])

    agent = ArchitectAgent("arch_1", db_session, mock_llm, mock_memory)
    doc = await agent.produce_tech_stack_document(_research())
    assert doc.language == "Python"
    assert doc.framework == "FastAPI"
