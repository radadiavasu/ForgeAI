"""ImpactAnalyser tests — mocked LLM."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.lead_agent import LeadAgent
from forgeai.lifecycle.change_classifier import ChangeClassifier
from forgeai.lifecycle.impact_analyser import ImpactAnalyser, _JARGON
from forgeai.lifecycle.schemas import ChangeType, ProjectStatus, RiskLevel
from forgeai.llm.schemas import APISurface, Component, LLMResponse, MasterDocument, TechStack
from forgeai.models.task import TaskComplexity
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_OUTPUT, KEY_WORK_OUTPUT


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
                endpoint="/tasks/{id}/complete",
                method="POST",
                request_schema={},
                response_schema={},
                description="complete",
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


@pytest.mark.asyncio
async def test_analyse_returns_impact_analysis(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "affected_task_titles": ["Complete task endpoint"],
                "conflicting_task_titles": [],
                "new_tasks_required": [],
                "estimated_cost_usd": 0.03,
                "estimated_time_minutes": 4,
            }
        ),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    lead = LeadAgent("lead_1", db_session)
    project_id = uuid.uuid4()
    task = await lead.create_task(
        "Complete task endpoint",
        None,
        TaskComplexity.LOW,
        "backend_agent_1",
        project_id=project_id,
    )
    await lead.approve_phase_transition(task.id)
    sm = TaskStateMachine(db_session)
    await sm.transition(task.id, TaskState.IN_PROGRESS, "backend_agent_1")
    await sm.transition(
        task.id,
        TaskState.IN_REVIEW,
        "backend_agent_1",
        **{KEY_WORK_OUTPUT: "code"},
    )
    await sm.transition(task.id, TaskState.TESTING, "qa_1")
    await sm.transition(task.id, TaskState.DONE, "qa_1", **{KEY_OUTPUT: "ok"})

    classification = await ChangeClassifier(mock_llm).classify(
        "fix endpoint",
        _master(),
        ProjectStatus.LIVE,
    )
    impact = await ImpactAnalyser(mock_llm, db_session).analyse(
        "fix endpoint",
        classification,
        str(project_id),
        _master(),
    )
    assert impact.project_id == str(project_id)
    assert impact.affected_task_ids
    assert impact.human_message.strip()
    assert impact.estimated_cost_usd > 0
    assert impact.estimated_time_minutes > 0


@pytest.mark.asyncio
async def test_human_message_no_jargon(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "affected_task_titles": [],
                "conflicting_task_titles": [],
                "new_tasks_required": ["New task"],
                "estimated_cost_usd": 0.1,
                "estimated_time_minutes": 10,
            }
        ),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    from forgeai.lifecycle.schemas import ChangeClassification

    classification = ChangeClassification(
        change_type=ChangeType.LARGE_FEATURE,
        risk_level=RiskLevel.HIGH,
        reasoning="large",
        requires_human_confirmation=True,
    )
    impact = await ImpactAnalyser(mock_llm, db_session).analyse(
        "add feature",
        classification,
        str(uuid.uuid4()),
        _master(),
    )
    lower = impact.human_message.lower()
    for word in _JARGON:
        assert word not in lower, f"found jargon: {word}"
