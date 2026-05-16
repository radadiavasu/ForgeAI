"""ChangeClassifier tests — mocked LLM."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from forgeai.lifecycle.change_classifier import ChangeClassifier
from forgeai.lifecycle.schemas import ChangeType, ProjectStatus, RiskLevel
from forgeai.llm.schemas import APISurface, Component, LLMResponse, MasterDocument, TechStack


def _master() -> MasterDocument:
    return MasterDocument(
        project_name="App",
        project_summary="Tasks",
        components=[
            Component(
                name="API",
                responsibility="REST",
                dependencies=[],
                acceptance_criteria=["works"],
            )
        ],
        api_surfaces=[
            APISurface(
                endpoint="/tasks",
                method="GET",
                request_schema={},
                response_schema={},
                description="list",
            )
        ],
        tech_stack=TechStack(
            language="Python",
            framework="FastAPI",
            database="PostgreSQL",
            testing_framework="pytest",
            rationale="r",
            rejected_alternatives=[],
        ),
    )


def _classify_json(
    change_type: str,
    risk: str,
    *,
    tasks: int = 1,
) -> str:
    return json.dumps(
        {
            "change_type": change_type,
            "risk_level": risk,
            "reasoning": "classified by test",
            "estimated_new_tasks": tasks,
        }
    )


@pytest.fixture
def mock_llm() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_classify_bugfix(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_classify_json("BUGFIX", "LOW"),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    result = await ChangeClassifier(mock_llm).classify(
        "Fix 404 on missing task",
        _master(),
        ProjectStatus.LIVE,
    )
    assert result.change_type == ChangeType.BUGFIX
    assert result.risk_level == RiskLevel.LOW
    assert result.requires_human_confirmation is False


@pytest.mark.asyncio
async def test_classify_small_feature(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_classify_json("SMALL_FEATURE", "MEDIUM", tasks=2),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    result = await ChangeClassifier(mock_llm).classify("Add export button", _master(), ProjectStatus.LIVE)
    assert result.change_type == ChangeType.SMALL_FEATURE


@pytest.mark.asyncio
async def test_classify_large_feature(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_classify_json("LARGE_FEATURE", "HIGH", tasks=8),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    result = await ChangeClassifier(mock_llm).classify(
        "Add team collaboration",
        _master(),
        ProjectStatus.LIVE,
    )
    assert result.change_type == ChangeType.LARGE_FEATURE
    assert result.requires_human_confirmation is True


@pytest.mark.asyncio
async def test_classify_architectural(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_classify_json("ARCHITECTURAL", "ARCHITECTURAL"),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    result = await ChangeClassifier(mock_llm).classify(
        "Migrate from SQL to graph database",
        _master(),
        ProjectStatus.LIVE,
    )
    assert result.change_type == ChangeType.ARCHITECTURAL
    assert result.requires_human_confirmation is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("risk", "expects_confirmation"),
    [
        ("LOW", False),
        ("MEDIUM", True),
        ("HIGH", True),
        ("ARCHITECTURAL", True),
    ],
)
async def test_risk_human_confirmation(
    mock_llm: AsyncMock, risk: str, expects_confirmation: bool
) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_classify_json("SMALL_FEATURE", risk),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    result = await ChangeClassifier(mock_llm).classify("change", _master(), ProjectStatus.LIVE)
    assert result.requires_human_confirmation is expects_confirmation


@pytest.mark.asyncio
async def test_reasoning_non_empty(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_classify_json("BUGFIX", "LOW"),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    result = await ChangeClassifier(mock_llm).classify("fix bug", _master(), ProjectStatus.LIVE)
    assert result.reasoning.strip()
