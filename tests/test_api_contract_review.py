"""API contract review at human gate — mocked LLM."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.lead_agent import LeadAgent
from forgeai.contracts.registry import ComponentRegistry
from forgeai.contracts.schemas import NavigationContract, RouteDefinition
from forgeai.llm.schemas import LLMResponse
from forgeai.models.project_artefact import ProjectArtefactModel
from forgeai.orchestration.phase_gate import PhaseGate
from forgeai.orchestration.schemas import FrontendPhaseResult


def _frontend_result(project_id: str) -> FrontendPhaseResult:
    return FrontendPhaseResult(
        project_id=project_id,
        completed_tasks=[],
        total_tasks=3,
        qa_cycles=1,
        components_registered=[],
        agents_used=[],
        phase_duration_seconds=0.5,
    )


def _nav(project_id: str) -> NavigationContract:
    return NavigationContract(
        project_id=project_id,
        routes=[
            RouteDefinition(
                path="/",
                owner_agent_id="frontend_agent_1",
                component_name="DashboardPage",
            )
        ],
        shared_layout_owner="frontend_agent_1",
    )


@pytest.mark.asyncio
async def test_review_api_contract_uses_medium_complexity(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "requires_update": False,
                "updated_contract": {"endpoints": ["/tasks"]},
                "changes_made": [],
            }
        ),
        model_used="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    gate = PhaseGate(lead, mock_llm, db_session)
    pid = str(uuid.uuid4())
    await gate.review_api_contract(
        {"endpoints": ["/tasks"]},
        _frontend_result(pid),
        pid,
    )
    assert mock_llm.complete.await_args.kwargs.get("complexity") == "MEDIUM"


@pytest.mark.asyncio
async def test_api_contract_review_populated_changes_list(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "requires_update": True,
                "updated_contract": {"endpoints": ["/tasks", "/history"]},
                "changes_made": ["Added GET /history for completed tasks UI"],
            }
        ),
        model_used="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    gate = PhaseGate(lead, mock_llm, db_session)
    pid = str(uuid.uuid4())
    review = await gate.review_api_contract(
        {"endpoints": ["/tasks"]},
        _frontend_result(pid),
        pid,
    )
    assert review.changes_made
    assert "history" in review.changes_made[0].lower()


@pytest.mark.asyncio
async def test_requires_update_false_when_no_changes(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "requires_update": False,
                "updated_contract": {"endpoints": ["/tasks"]},
                "changes_made": [],
            }
        ),
        model_used="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    gate = PhaseGate(lead, mock_llm, db_session)
    pid = str(uuid.uuid4())
    review = await gate.review_api_contract(
        {"endpoints": ["/tasks"]},
        _frontend_result(pid),
        pid,
    )
    assert review.requires_update is False


@pytest.mark.asyncio
async def test_requires_update_true_when_gaps_identified(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "requires_update": True,
                "updated_contract": {"endpoints": ["/tasks", "/settings"]},
                "changes_made": ["Added settings endpoint"],
            }
        ),
        model_used="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    gate = PhaseGate(lead, mock_llm, db_session)
    review = await gate.review_api_contract(
        {"endpoints": []},
        _frontend_result(str(uuid.uuid4())),
        str(uuid.uuid4()),
    )
    assert review.requires_update is True


@pytest.mark.asyncio
async def test_updated_contract_written_to_project_memory(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    updated = {"endpoints": ["/tasks", "/prefs"]}
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "requires_update": True,
                "updated_contract": updated,
                "changes_made": ["Added preferences endpoint"],
            }
        ),
        model_used="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    project_id = uuid.uuid4()
    fe_result = _frontend_result(str(project_id))
    await lead.execute_human_gate(
        fe_result,
        ComponentRegistry(db_session),
        _nav(str(project_id)),
        {"endpoints": ["/tasks"]},
        project_id,
        human_approval_callback=lambda _s: _approve(),
    )
    res = await db_session.execute(
        select(ProjectArtefactModel).where(
            ProjectArtefactModel.project_id == project_id,
            ProjectArtefactModel.artefact_type == "api_contract",
            ProjectArtefactModel.is_current.is_(True),
        )
    )
    row = res.scalar_one_or_none()
    assert row is not None
    assert row.content.get("endpoints") == ["/tasks", "/prefs"]


async def _approve() -> bool:
    return True
