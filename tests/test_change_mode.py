"""CHANGE mode tests — mocked LLM."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.lead_agent import LeadAgent
from forgeai.lifecycle.change_executor import ChangeExecutor
from forgeai.lifecycle.project_registry import ProjectRegistry
from forgeai.lifecycle.schemas import (
    ChangeClassification,
    ChangeDecision,
    ChangeType,
    HumanChangeApproval,
    ImpactAnalysis,
    ProjectStatus,
    RiskLevel,
)
from forgeai.llm.schemas import LLMResponse


@pytest.mark.asyncio
async def test_change_spec_produced(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "summary": "Team collaboration",
                "new_components": ["TeamList"],
                "new_api_surfaces": ["/api/teams"],
                "new_tasks": [
                    {
                        "title": "Team API",
                        "description": "teams",
                        "complexity": "MEDIUM",
                        "phase": "BACKEND_PHASE",
                        "dependencies": [],
                    }
                ],
                "rework_tasks": [],
                "estimated_cost_usd": 0.5,
                "estimated_time_minutes": 30,
            }
        ),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    from forgeai.escalation import EscalationLadder, EscalationPersistence
    from forgeai.escalation.loop_counter import LoopCounter
    from forgeai.llm.schemas import MasterDocument, TechStack

    master = MasterDocument(
        project_name="App",
        project_summary="Tasks",
        tech_stack=TechStack(
            language="Python",
            framework="FastAPI",
            database="PostgreSQL",
            testing_framework="pytest",
            rationale="r",
            rejected_alternatives=[],
        ),
    )
    impact = ImpactAnalysis(
        project_id=str(uuid.uuid4()),
        change_request="Add teams",
        classification=ChangeClassification(
            change_type=ChangeType.LARGE_FEATURE,
            risk_level=RiskLevel.HIGH,
            reasoning="large",
            requires_human_confirmation=True,
        ),
    )
    approval = HumanChangeApproval(
        project_id=impact.project_id,
        change_request=impact.change_request,
        impact_analysis=impact,
        decision=ChangeDecision.PROCEED,
    )

    async def _scope(_spec: object) -> bool:
        return True

    exec_ = ChangeExecutor(
        lead,
        mock_llm,
        lead.build_qa_orchestrator(LoopCounter(), EscalationPersistence(db_session)),
        db_session,
    )
    result = await exec_.execute_change(
        impact.change_request,
        approval,
        impact.project_id,
        master,
        _scope,
    )
    assert result.change_spec is not None
    assert len(result.change_spec.new_tasks) >= 1
    assert len(result.new_tasks_completed) >= 1


@pytest.mark.asyncio
async def test_rework_applied_in_change_mode(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_1", db_session, llm_client=AsyncMock())
    pid = uuid.uuid4()
    from forgeai.models.task import TaskComplexity
    from forgeai.state_machine.machine import TaskStateMachine
    from forgeai.state_machine.states import TaskState
    from forgeai.state_machine.transitions import KEY_OUTPUT, KEY_WORK_OUTPUT

    task = await lead.create_task(
        "Dashboard API",
        None,
        TaskComplexity.LOW,
        "backend_agent_1",
        project_id=pid,
    )
    await lead.approve_phase_transition(task.id)
    sm = TaskStateMachine(db_session)
    await sm.transition(task.id, TaskState.IN_PROGRESS, "backend_agent_1")
    await sm.transition(
        task.id,
        TaskState.IN_REVIEW,
        "backend_agent_1",
        **{KEY_WORK_OUTPUT: "c"},
    )
    await sm.transition(task.id, TaskState.TESTING, "qa_1")
    await sm.transition(task.id, TaskState.DONE, "qa_1", **{KEY_OUTPUT: "ok"})

    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "summary": "s",
                "new_tasks": [],
                "rework_tasks": ["Dashboard API"],
                "estimated_cost_usd": 0.1,
                "estimated_time_minutes": 5,
            }
        ),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    from forgeai.escalation import EscalationLadder, EscalationPersistence
    from forgeai.escalation.loop_counter import LoopCounter
    from forgeai.llm.schemas import MasterDocument, TechStack

    master = MasterDocument(
        project_name="A",
        project_summary="s",
        tech_stack=TechStack(
            language="Python",
            framework="FastAPI",
            database="PostgreSQL",
            testing_framework="pytest",
            rationale="r",
            rejected_alternatives=[],
        ),
    )
    impact = ImpactAnalysis(
        project_id=str(pid),
        change_request="update dashboard",
        classification=ChangeClassification(
            change_type=ChangeType.LARGE_FEATURE,
            risk_level=RiskLevel.HIGH,
            reasoning="r",
            requires_human_confirmation=True,
        ),
        affected_task_ids=[str(task.id)],
    )
    approval = HumanChangeApproval(
        project_id=str(pid),
        change_request="update",
        impact_analysis=impact,
        decision=ChangeDecision.PROCEED,
    )
    exec_ = ChangeExecutor(
        lead,
        mock_llm,
        lead.build_qa_orchestrator(LoopCounter(), EscalationPersistence(db_session)),
        db_session,
    )
    result = await exec_.execute_change(
        "update",
        approval,
        str(pid),
        master,
        lambda _s: _async_true(),
    )
    assert str(task.id) in result.rework_tasks_completed


async def _async_true(_spec: object | None = None) -> bool:
    return True


@pytest.mark.asyncio
async def test_change_spec_prompt_references_architect() -> None:
    from forgeai.lifecycle.change_executor import CHANGE_SPEC_PROMPT

    assert "Architect" in CHANGE_SPEC_PROMPT


@pytest.mark.asyncio
async def test_new_tasks_created_from_change_spec(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "summary": "Export",
                "new_tasks": [
                    {
                        "title": "CSV export API",
                        "description": "export",
                        "complexity": "LOW",
                        "phase": "BACKEND_PHASE",
                        "dependencies": [],
                    }
                ],
                "rework_tasks": [],
                "estimated_cost_usd": 0.2,
                "estimated_time_minutes": 15,
            }
        ),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    from forgeai.escalation import EscalationLadder, EscalationPersistence
    from forgeai.escalation.loop_counter import LoopCounter
    from forgeai.llm.schemas import MasterDocument, TechStack

    master = MasterDocument(
        project_name="A",
        project_summary="s",
        tech_stack=TechStack(
            language="Python",
            framework="FastAPI",
            database="PostgreSQL",
            testing_framework="pytest",
            rationale="r",
            rejected_alternatives=[],
        ),
    )
    impact = ImpactAnalysis(
        project_id=str(uuid.uuid4()),
        change_request="export",
        classification=ChangeClassification(
            change_type=ChangeType.LARGE_FEATURE,
            risk_level=RiskLevel.HIGH,
            reasoning="r",
            requires_human_confirmation=True,
        ),
    )
    approval = HumanChangeApproval(
        project_id=impact.project_id,
        change_request="export",
        impact_analysis=impact,
        decision=ChangeDecision.PROCEED,
    )
    exec_ = ChangeExecutor(
        lead,
        mock_llm,
        lead.build_qa_orchestrator(LoopCounter(), EscalationPersistence(db_session)),
        db_session,
    )
    result = await exec_.execute_change(
        "export",
        approval,
        impact.project_id,
        master,
        _async_true,
    )
    assert len(result.new_tasks_completed) == 1


@pytest.mark.asyncio
async def test_project_stays_live_after_change(db_session: AsyncSession) -> None:
    from forgeai.lifecycle.project_registry import ProjectRegistry

    reg = ProjectRegistry(db_session)
    project = await reg.create_project("LiveChange", "brief")
    await reg.set_live(project.id, "v1")
    mock_llm = AsyncMock()
    mock_llm.complete.side_effect = [
        LLMResponse(
            content=json.dumps(
                {
                    "change_type": "LARGE_FEATURE",
                    "risk_level": "HIGH",
                    "reasoning": "r",
                    "estimated_new_tasks": 1,
                }
            ),
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        ),
        LLMResponse(
            content=json.dumps(
                {
                    "affected_task_titles": [],
                    "conflicting_task_titles": [],
                    "new_tasks_required": [],
                    "estimated_cost_usd": 0.5,
                    "estimated_time_minutes": 20,
                }
            ),
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        ),
        LLMResponse(
            content=json.dumps(
                {
                    "summary": "Teams",
                    "new_tasks": [
                        {
                            "title": "Team API",
                            "description": "teams",
                            "complexity": "MEDIUM",
                            "phase": "BACKEND_PHASE",
                            "dependencies": [],
                        }
                    ],
                    "rework_tasks": [],
                    "estimated_cost_usd": 0.5,
                    "estimated_time_minutes": 20,
                }
            ),
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        ),
    ]
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    from forgeai.llm.schemas import MasterDocument, TechStack

    master = MasterDocument(
        project_name="A",
        project_summary="s",
        tech_stack=TechStack(
            language="Python",
            framework="FastAPI",
            database="PostgreSQL",
            testing_framework="pytest",
            rationale="r",
            rejected_alternatives=[],
        ),
    )

    async def _approve(_msg: str) -> ChangeDecision:
        return ChangeDecision.PROCEED

    await lead.accept_change_request(
        "Add teams",
        uuid.UUID(project.id),
        master,
        _approve,
        human_scope_callback=_async_true,
    )
    row = await reg.get_project(project.id)
    assert row is not None
    assert row.status == ProjectStatus.LIVE


@pytest.mark.asyncio
async def test_change_history_written_in_change_mode(db_session: AsyncSession) -> None:
    from forgeai.lifecycle.project_registry import ProjectRegistry
    from forgeai.models.project_artefact import ProjectArtefactModel
    from sqlalchemy import select

    reg = ProjectRegistry(db_session)
    project = await reg.create_project("HistChange", "brief")
    await reg.set_live(project.id, "v1")
    mock_llm = AsyncMock()
    mock_llm.complete.side_effect = [
        LLMResponse(
            content=json.dumps(
                {
                    "change_type": "LARGE_FEATURE",
                    "risk_level": "HIGH",
                    "reasoning": "r",
                    "estimated_new_tasks": 1,
                }
            ),
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        ),
        LLMResponse(
            content=json.dumps(
                {
                    "affected_task_titles": [],
                    "conflicting_task_titles": [],
                    "new_tasks_required": [],
                    "estimated_cost_usd": 0.5,
                    "estimated_time_minutes": 20,
                }
            ),
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        ),
        LLMResponse(
            content=json.dumps(
                {
                    "summary": "Teams",
                    "new_tasks": [],
                    "rework_tasks": [],
                    "estimated_cost_usd": 0.5,
                    "estimated_time_minutes": 20,
                }
            ),
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        ),
    ]
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    from forgeai.llm.schemas import MasterDocument, TechStack

    master = MasterDocument(
        project_name="A",
        project_summary="s",
        tech_stack=TechStack(
            language="Python",
            framework="FastAPI",
            database="PostgreSQL",
            testing_framework="pytest",
            rationale="r",
            rejected_alternatives=[],
        ),
    )

    async def _approve(_msg: str) -> ChangeDecision:
        return ChangeDecision.PROCEED

    entry = await lead.accept_change_request(
        "Add teams",
        uuid.UUID(project.id),
        master,
        _approve,
        human_scope_callback=_async_true,
    )
    res = await db_session.execute(
        select(ProjectArtefactModel).where(
            ProjectArtefactModel.project_id == uuid.UUID(project.id),
            ProjectArtefactModel.artefact_type == f"change_history:{entry.entry_id}",
        )
    )
    assert res.scalar_one_or_none() is not None
