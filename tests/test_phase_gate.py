"""PhaseGate and human gate tests — mocked LLM."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.lead_agent import LeadAgent
from forgeai.contracts.registry import ComponentRegistry
from forgeai.contracts.schemas import NavigationContract, RouteDefinition
from forgeai.llm.schemas import LLMResponse
from forgeai.models.task import TaskComplexity
from forgeai.orchestration.phase_gate import PhaseGate, _JARGON
from forgeai.orchestration.schemas import FrontendPhaseResult
from forgeai.state_machine.states import TaskState


def _frontend_result(project_id: str, task_ids: list[str]) -> FrontendPhaseResult:
    return FrontendPhaseResult(
        project_id=project_id,
        completed_tasks=task_ids,
        total_tasks=len(task_ids),
        qa_cycles=2,
        components_registered=["AppLayout"],
        agents_used=["frontend_agent_1"],
        phase_duration_seconds=1.0,
    )


def _nav_contract(project_id: str) -> NavigationContract:
    return NavigationContract(
        project_id=project_id,
        routes=[
            RouteDefinition(
                path="/",
                owner_agent_id="frontend_agent_1",
                component_name="DashboardPage",
                is_root_layout=True,
            ),
        ],
        shared_layout_owner="frontend_agent_1",
    )


@pytest.mark.asyncio
async def test_compile_report_returns_phase_completion_report(
    db_session: AsyncSession,
) -> None:
    lead = LeadAgent("lead_1", db_session)
    mock_llm = AsyncMock()
    gate = PhaseGate(lead, mock_llm, db_session)
    project_id = uuid.uuid4()
    task = await lead.create_task(
        "Build Dashboard page",
        None,
        TaskComplexity.LOW,
        "frontend_agent_2",
        project_id=project_id,
    )
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    from forgeai.state_machine.machine import TaskStateMachine
    from forgeai.state_machine.transitions import KEY_OUTPUT, KEY_WORK_OUTPUT

    sm = TaskStateMachine(db_session)
    await sm.transition(
        task.id,
        TaskState.IN_REVIEW,
        "frontend_agent_2",
        **{KEY_WORK_OUTPUT: "code"},
    )
    await sm.transition(task.id, TaskState.TESTING, "qa_1")
    await sm.transition(
        task.id,
        TaskState.DONE,
        "qa_1",
        **{KEY_OUTPUT: "done"},
    )
    reg = ComponentRegistry(db_session)
    await reg.register(
        str(project_id),
        "AppLayout",
        "frontend_agent_1",
        "Shell",
        "src/AppLayout.jsx",
    )
    report = await gate.compile_report(
        _frontend_result(str(project_id), [str(task.id)]),
        reg,
        _nav_contract(str(project_id)),
        str(project_id),
    )
    assert report.phase == "FRONTEND_PHASE"
    assert report.total_tasks >= 1
    assert len(report.completed_tasks) >= 1


@pytest.mark.asyncio
async def test_report_contains_component_registry(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_1", db_session)
    gate = PhaseGate(lead, AsyncMock(), db_session)
    project_id = uuid.uuid4()
    reg = ComponentRegistry(db_session)
    await reg.register(
        str(project_id),
        "NavBar",
        "frontend_agent_1",
        "Nav",
        "src/NavBar.jsx",
    )
    report = await gate.compile_report(
        _frontend_result(str(project_id), []),
        reg,
        _nav_contract(str(project_id)),
        str(project_id),
    )
    names = {c.component_name for c in report.components_registry}
    assert "NavBar" in names


@pytest.mark.asyncio
async def test_format_report_for_human_non_empty(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_1", db_session)
    gate = PhaseGate(lead, AsyncMock(), db_session)
    report = await gate.compile_report(
        _frontend_result(str(uuid.uuid4()), []),
        ComponentRegistry(db_session),
        _nav_contract("p1"),
        str(uuid.uuid4()),
    )
    text = gate.format_report_for_human(report)
    assert text.strip()
    assert "HUMAN GATE" in text


@pytest.mark.asyncio
async def test_format_report_for_human_no_jargon(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_1", db_session)
    gate = PhaseGate(lead, AsyncMock(), db_session)
    report = await gate.compile_report(
        _frontend_result(str(uuid.uuid4()), []),
        ComponentRegistry(db_session),
        _nav_contract("p1"),
        str(uuid.uuid4()),
    )
    text = gate.format_report_for_human(report).lower()
    for word in _JARGON:
        assert word not in text, f"found jargon: {word}"


@pytest.mark.asyncio
async def test_present_to_human_calls_callback(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_1", db_session)
    gate = PhaseGate(lead, AsyncMock(), db_session)
    report = await gate.compile_report(
        _frontend_result(str(uuid.uuid4()), []),
        ComponentRegistry(db_session),
        _nav_contract("p1"),
        str(uuid.uuid4()),
    )
    called: list[str] = []

    async def _approve(summary: str) -> bool:
        called.append(summary)
        return True

    result = await gate.present_to_human(report, _approve)
    assert result.approved is True
    assert len(called) == 1


@pytest.mark.asyncio
async def test_backend_tasks_unlock_on_gate_approval(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_1", db_session)
    project_id = uuid.uuid4()
    backend = await lead.create_task(
        "REST API for tasks",
        "CRUD",
        TaskComplexity.MEDIUM,
        "backend_agent_1",
        project_id=project_id,
    )
    assert backend.current_state == TaskState.PHASE_LOCKED
    count = await lead._unlock_backend_tasks(project_id)
    assert count == 1
    await db_session.refresh(backend)
    assert backend.current_state == TaskState.TODO


@pytest.mark.asyncio
async def test_backend_tasks_remain_locked_when_gate_not_approved(
    db_session: AsyncSession,
) -> None:
    lead = LeadAgent("lead_1", db_session)
    project_id = uuid.uuid4()
    backend = await lead.create_task(
        "REST API for tasks",
        None,
        TaskComplexity.MEDIUM,
        "backend_agent_1",
        project_id=project_id,
    )
    feedback_tasks = await lead._create_feedback_tasks(
        "Add missing settings validation",
        project_id,
    )
    await db_session.refresh(backend)
    assert backend.current_state == TaskState.PHASE_LOCKED
    assert len(feedback_tasks) == 1
    assert "feedback" in feedback_tasks[0].title.lower()


@pytest.mark.asyncio
async def test_execute_human_gate_unlocks_backend(db_session: AsyncSession) -> None:
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "requires_update": False,
                "updated_contract": {"endpoints": []},
                "changes_made": [],
            }
        ),
        model_used="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    project_id = uuid.uuid4()
    backend = await lead.create_task(
        "REST API for tasks",
        None,
        TaskComplexity.MEDIUM,
        "backend_agent_1",
        project_id=project_id,
    )
    fe_result = _frontend_result(str(project_id), [])
    result = await lead.execute_human_gate(
        fe_result,
        ComponentRegistry(db_session),
        _nav_contract(str(project_id)),
        {"endpoints": []},
        project_id,
        human_approval_callback=lambda _s: _async_true(),
    )
    assert result.approved is True
    await db_session.refresh(backend)
    assert backend.current_state == TaskState.TODO


async def _async_true() -> bool:
    return True
