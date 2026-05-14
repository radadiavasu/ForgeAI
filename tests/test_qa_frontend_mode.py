"""QA_Agent frontend routing and Playwright test generation (mocked)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.qa_agent import QAAgent, QA_PLAYWRIGHT_GENERATION_PROMPT
from forgeai.contracts.schemas import NavigationContract, PageSpec, RouteDefinition
from forgeai.llm.schemas import LLMResponse
from forgeai.sandbox.schemas import RunnerOutput, SandboxTestCaseResult


_VALID_PLAYWRIGHT = """
import { test, expect } from '@playwright/test';

test.describe('Dashboard', () => {
  test('page renders without errors', async ({ page }) => {
    await page.goto('/');
    await expect(page).not.toHaveTitle(/error/i);
  });
  test('header section', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('header')).toBeVisible();
  });
  test('task-list section', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('[data-section="task-list"]')).toBeVisible();
  });
  test('nav links', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('a[href="/history"]')).toBeVisible();
  });
});
"""


def _page_and_nav() -> tuple[PageSpec, NavigationContract]:
    page = PageSpec(
        name="Dashboard",
        route="/",
        sections=["header", "task-list"],
        interactions=["add task"],
        acceptance_criteria=["List renders"],
    )
    nav = NavigationContract(
        project_id="p1",
        routes=[
            RouteDefinition(path="/", owner_agent_id="fe1", component_name="Dash", is_root_layout=True),
            RouteDefinition(path="/history", owner_agent_id="fe1", component_name="Hist", is_root_layout=False),
        ],
        shared_layout_owner="fe1",
    )
    return page, nav


@pytest.mark.asyncio
async def test_review_routes_to_pytest_when_backend_phase(db_session: AsyncSession) -> None:
    tr = MagicMock()
    tr.run = AsyncMock(
        return_value=RunnerOutput(
            success=True,
            total_tests=1,
            passed_tests=1,
            failed_tests=0,
            test_cases=[SandboxTestCaseResult(name="t", passed=True)],
            stdout="",
            stderr="",
            execution_time_seconds=0.1,
        )
    )
    fs = MagicMock()
    fs.run = AsyncMock()
    qa = QAAgent("qa_agent_1", db_session, test_runner=tr, frontend_sandbox=fs)
    tid = uuid.uuid4()
    out = await qa.review(tid, "code", "tests", development_phase="BACKEND_PHASE")
    tr.run.assert_awaited_once()
    fs.run.assert_not_called()
    assert out.success is True


@pytest.mark.asyncio
async def test_review_routes_to_playwright_when_frontend_phase(db_session: AsyncSession) -> None:
    tr = MagicMock()
    tr.run = AsyncMock()
    fs = MagicMock()
    fs.run = AsyncMock(
        return_value=RunnerOutput(
            success=True,
            total_tests=1,
            passed_tests=1,
            failed_tests=0,
            test_cases=[SandboxTestCaseResult(name="x", passed=True)],
            stdout="{}",
            stderr="",
            execution_time_seconds=0.5,
        )
    )
    qa = QAAgent("qa_agent_1", db_session, test_runner=tr, frontend_sandbox=fs)
    tid = uuid.uuid4()
    out = await qa.review(tid, "export default function App(){}", _VALID_PLAYWRIGHT, development_phase="FRONTEND_PHASE")
    fs.run.assert_awaited_once()
    tr.run.assert_not_called()
    assert out.success is True


@pytest.mark.asyncio
async def test_generate_playwright_tests_calls_llm_low_first(db_session: AsyncSession) -> None:
    page, nav = _page_and_nav()
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=LLMResponse(
            content=_VALID_PLAYWRIGHT,
            model_used="haiku",
            input_tokens=10,
            output_tokens=50,
            estimated_cost_usd=0.0,
            tool_calls=[],
        )
    )
    qa = QAAgent("qa_agent_1", db_session, llm_client=llm)
    text = await qa.generate_playwright_tests(page, nav)
    llm.complete.assert_awaited()
    first_kw = llm.complete.await_args_list[0].kwargs
    assert first_kw["complexity"] == "LOW"
    assert first_kw["system_prompt"] == QA_PLAYWRIGHT_GENERATION_PROMPT
    assert "/" in first_kw["user_message"]
    assert "header" in text.lower() or "task-list" in text.lower()


@pytest.mark.asyncio
async def test_generated_tests_contain_route_and_test_case(db_session: AsyncSession) -> None:
    page, nav = _page_and_nav()
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=LLMResponse(
            content=_VALID_PLAYWRIGHT,
            model_used="haiku",
            input_tokens=10,
            output_tokens=50,
            estimated_cost_usd=0.0,
            tool_calls=[],
        )
    )
    qa = QAAgent("qa_agent_1", db_session, llm_client=llm)
    text = await qa.generate_playwright_tests(page, nav)
    assert page.route in text
    assert "test(" in text


@pytest.mark.asyncio
async def test_generated_tests_reference_page_sections(db_session: AsyncSession) -> None:
    page, nav = _page_and_nav()
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=LLMResponse(
            content=_VALID_PLAYWRIGHT,
            model_used="haiku",
            input_tokens=10,
            output_tokens=50,
            estimated_cost_usd=0.0,
            tool_calls=[],
        )
    )
    qa = QAAgent("qa_agent_1", db_session, llm_client=llm)
    text = await qa.generate_playwright_tests(page, nav)
    lowered = text.lower()
    assert "header" in lowered
    assert "task-list" in lowered or "task list" in lowered


@pytest.mark.asyncio
async def test_generate_retries_medium_when_low_invalid(db_session: AsyncSession) -> None:
    page, nav = _page_and_nav()
    llm = MagicMock()
    bad = "// not playwright"
    good = _VALID_PLAYWRIGHT
    llm.complete = AsyncMock(
        side_effect=[
            LLMResponse(content=bad, model_used="haiku", input_tokens=1, output_tokens=1, estimated_cost_usd=0.0, tool_calls=[]),
            LLMResponse(content=good, model_used="sonnet", input_tokens=1, output_tokens=1, estimated_cost_usd=0.0, tool_calls=[]),
        ]
    )
    qa = QAAgent("qa_agent_1", db_session, llm_client=llm)
    text = await qa.generate_playwright_tests(page, nav)
    assert len(llm.complete.await_args_list) == 2
    assert "test(" in text
    assert llm.complete.await_args_list[1].kwargs["complexity"] == "MEDIUM"
