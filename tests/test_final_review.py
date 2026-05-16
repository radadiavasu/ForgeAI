"""Final review stub tests — mocked LLM."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.lead_agent import LeadAgent
from forgeai.intelligence.final_review import FinalReviewer
from forgeai.llm.schemas import APISurface, Component, LLMResponse, MasterDocument, TechStack
from forgeai.models.task import TaskComplexity
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_OUTPUT, KEY_WORK_OUTPUT

_PID = "00000000-0000-0000-0000-0000000000aa"


def _master() -> MasterDocument:
    return MasterDocument(
        project_name="Task Manager",
        project_summary="Tasks app",
        components=[
            Component(
                name="API",
                responsibility="REST",
                dependencies=[],
                acceptance_criteria=["CRUD works"],
            )
        ],
        api_surfaces=[
            APISurface(
                endpoint="/tasks",
                method="GET",
                request_schema={},
                response_schema={},
                description="List tasks",
            )
        ],
        tech_stack=TechStack(
            language="Python",
            framework="FastAPI",
            database="PostgreSQL",
            testing_framework="pytest",
            rationale="test",
            rejected_alternatives=[],
        ),
    )


def _review_json(*, passed: bool, gaps: list[str] | None = None) -> str:
    return json.dumps(
        {
            "passed": passed,
            "consistency_checks": ["All components mapped"],
            "gaps_found": gaps or [],
            "remediation_tasks": ["Fix gap"] if gaps else [],
        }
    )


@pytest.mark.asyncio
async def test_review_returns_final_review_result() -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_review_json(passed=True),
        model_used="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    reviewer = FinalReviewer(mock_llm, AsyncMock())
    result = await reviewer.review(_PID, _master(), completed_tasks=[])
    assert result.project_id
    assert isinstance(result.passed, bool)


@pytest.mark.asyncio
async def test_passed_true_when_no_gaps() -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_review_json(passed=True),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    result = await FinalReviewer(mock_llm, AsyncMock()).review(_PID, _master(), completed_tasks=[])
    assert result.passed is True
    assert result.gaps_found == []


@pytest.mark.asyncio
async def test_passed_false_when_gaps_found() -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_review_json(passed=False, gaps=["Missing settings API"]),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    result = await FinalReviewer(mock_llm, AsyncMock()).review(_PID, _master(), completed_tasks=[])
    assert result.passed is False
    assert result.gaps_found


@pytest.mark.asyncio
async def test_consistency_checks_non_empty() -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_review_json(passed=True),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    result = await FinalReviewer(mock_llm, AsyncMock()).review(_PID, _master(), completed_tasks=[])
    assert len(result.consistency_checks) >= 1


@pytest.mark.asyncio
async def test_remediation_tasks_when_gaps(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_review_json(passed=False, gaps=["gap"]),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    result = await FinalReviewer(mock_llm, db_session).review(_PID, _master(), completed_tasks=[])
    assert len(result.remediation_tasks) >= 1


@pytest.mark.asyncio
async def test_review_uses_high_complexity() -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_review_json(passed=True),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    await FinalReviewer(mock_llm, AsyncMock()).review(_PID, _master(), completed_tasks=[])
    assert mock_llm.complete.await_args.kwargs.get("complexity") == "HIGH"


@pytest.mark.asyncio
async def test_lead_execute_final_review(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=_review_json(passed=True),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    project_id = uuid.uuid4()
    task = await lead.create_task("API task", None, TaskComplexity.LOW, "backend_agent_1", project_id)
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    sm = TaskStateMachine(db_session)
    await sm.transition(task.id, TaskState.IN_REVIEW, "backend_agent_1", **{KEY_WORK_OUTPUT: "code"})
    await sm.transition(task.id, TaskState.TESTING, "qa_1")
    await sm.transition(task.id, TaskState.DONE, "qa_1", **{KEY_OUTPUT: "done"})
    result = await lead.execute_final_review(project_id, _master())
    assert result.passed is True
