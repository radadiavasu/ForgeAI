"""ReadmeGenerator tests — mocked LLM."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest

from forgeai.delivery.readme_generator import ReadmeGenerator, _JARGON
from forgeai.delivery.schemas import DeploymentPackage
from forgeai.llm.schemas import LLMResponse, TechStackDocument


def _tech() -> TechStackDocument:
    return TechStackDocument(
        language="Python",
        framework="FastAPI",
        database="PostgreSQL",
        testing_framework="pytest",
        rationale="test",
        rejected_alternatives=[],
    )


def _pkg() -> DeploymentPackage:
    return DeploymentPackage(
        project_id="p1",
        output_dir="/tmp/out",
        env_example_path="/tmp/out/.env.example",
    )


@pytest.mark.asyncio
async def test_generate_calls_llm_with_medium_complexity() -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content="# My App\n\nSimple task app.\n\n## Setup\n1. Copy\n2. docker compose up\n3. Open",
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    gen = ReadmeGenerator(mock_llm)
    text = await gen.generate("My App", "Manage tasks easily.", _tech(), _pkg())
    mock_llm.complete.assert_awaited()
    assert mock_llm.complete.await_args.kwargs["complexity"] == "MEDIUM"
    assert "My App" in text


@pytest.mark.asyncio
async def test_readme_contains_docker_compose_up() -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content="# App\n\nDesc.\n\n## Setup\n1. a\n2. docker compose up\n3. b",
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    gen = ReadmeGenerator(mock_llm)
    text = await gen.generate("App", "Brief", _tech(), _pkg())
    assert "docker compose up" in text.lower()


@pytest.mark.asyncio
async def test_readme_no_jargon() -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content="# App\n\nUses PostgreSQL agent LLM.\n\n## Setup\n1. x\n2. docker compose up",
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    gen = ReadmeGenerator(mock_llm)
    text = (await gen.generate("App", "Brief", _tech(), _pkg())).lower()
    for word in _JARGON:
        assert word not in text, f"found jargon: {word}"


@pytest.mark.asyncio
async def test_readme_max_three_setup_steps() -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=(
            "# App\n\nDesc.\n\n## Setup\n"
            "1. one\n2. two\n3. three\n4. four\n\n## Stopping\n"
            "docker compose down"
        ),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    gen = ReadmeGenerator(mock_llm)
    text = await gen.generate("App", "Brief", _tech(), _pkg())
    in_setup = False
    steps = 0
    for line in text.splitlines():
        if line.strip().lower().startswith("## setup"):
            in_setup = True
            continue
        if in_setup and line.startswith("## "):
            break
        if in_setup and re.match(r"^\d+\.\s", line.strip()):
            steps += 1
    assert steps <= 3
