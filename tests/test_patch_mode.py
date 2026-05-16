"""PATCH mode tests — mocked LLM, no Docker."""

from __future__ import annotations

import json
import uuid
from unittest.mock import ANY, AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.lead_agent import LeadAgent
from forgeai.escalation import EscalationLadder, EscalationPersistence
from forgeai.escalation.loop_counter import LoopCounter
from forgeai.lifecycle.impact_analyser import ImpactAnalyser
from forgeai.lifecycle.patch_executor import PatchExecutor
from forgeai.lifecycle.project_registry import ProjectRegistry
from forgeai.lifecycle.schemas import (
    ChangeClassification,
    ChangeDecision,
    ChangeType,
    HumanChangeApproval,
    ProjectStatus,
    RiskLevel,
)
from forgeai.models.project_artefact import ProjectArtefactModel
from forgeai.models.task import TaskComplexity
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_OUTPUT, KEY_WORK_OUTPUT


def _classification() -> ChangeClassification:
    return ChangeClassification(
        change_type=ChangeType.BUGFIX,
        risk_level=RiskLevel.LOW,
        reasoning="bugfix",
        requires_human_confirmation=False,
    )


@pytest.mark.asyncio
async def test_rework_transition_applied(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_1", db_session, llm_client=AsyncMock())
    reg = ProjectRegistry(db_session)
    project = await reg.create_project("App", "brief")
    await reg.set_live(project.id, "v1")
    pid = uuid.UUID(project.id)

    task = await lead.create_task(
        "Complete task endpoint",
        "Returns 200 for missing id",
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
        **{KEY_WORK_OUTPUT: "code"},
    )
    await sm.transition(task.id, TaskState.TESTING, "qa_1")
    await sm.transition(task.id, TaskState.DONE, "qa_1", **{KEY_OUTPUT: "ok"})

    from forgeai.lifecycle.schemas import ImpactAnalysis

    impact = ImpactAnalysis(
        project_id=project.id,
        change_request="Fix 404",
        classification=_classification(),
        affected_task_ids=[str(task.id)],
        affected_task_titles=[task.title],
        new_tasks_required=[],
        estimated_cost_usd=0.02,
        estimated_time_minutes=3,
    )
    approval = HumanChangeApproval(
        project_id=project.id,
        change_request="Fix 404",
        impact_analysis=impact,
        decision=ChangeDecision.PROCEED,
    )
    loop = LoopCounter()
    ladder = EscalationLadder(loop, EscalationPersistence(db_session))
    qa_orch = lead.build_qa_orchestrator(loop, ladder)
    patch = PatchExecutor(lead, qa_orch, db_session)
    result = await patch.execute(impact, approval, project.id)
    await db_session.refresh(task)
    assert task.current_state in (TaskState.DONE, TaskState.REWORK)
    assert result.regression_tests_passed is True


@pytest.mark.asyncio
async def test_new_tasks_for_small_feature(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_1", db_session)
    pid = uuid.uuid4()
    from forgeai.lifecycle.schemas import ImpactAnalysis

    impact = ImpactAnalysis(
        project_id=str(pid),
        change_request="Add export",
        classification=ChangeClassification(
            change_type=ChangeType.SMALL_FEATURE,
            risk_level=RiskLevel.LOW,
            reasoning="small",
            requires_human_confirmation=False,
        ),
        affected_task_ids=[],
        new_tasks_required=["Add CSV export endpoint"],
    )
    approval = HumanChangeApproval(
        project_id=str(pid),
        change_request="Add export",
        impact_analysis=impact,
        decision=ChangeDecision.PROCEED,
    )
    mock_llm = AsyncMock()
    lead_with_llm = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    loop = LoopCounter()
    ladder = EscalationLadder(loop, EscalationPersistence(db_session))
    patch = PatchExecutor(
        lead_with_llm,
        lead_with_llm.build_qa_orchestrator(loop, ladder),
        db_session,
    )
    result = await patch.execute(impact, approval, str(pid))
    assert len(result.new_tasks_completed) >= 1


@pytest.mark.asyncio
async def test_change_history_written(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_1", db_session, llm_client=AsyncMock())
    lead._llm_client.complete.return_value = __import__(
        "forgeai.llm.schemas", fromlist=["LLMResponse"]
    ).LLMResponse(
        content=json.dumps({"passed": True, "consistency_checks": ["ok"], "gaps_found": []}),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    reg = ProjectRegistry(db_session)
    project = await reg.create_project("Hist", "brief")
    await reg.set_live(project.id, "v1")
    pid = uuid.UUID(project.id)

    async def _approve(_msg: str):
        from forgeai.lifecycle.schemas import ChangeDecision

        return ChangeDecision.PROCEED

    from forgeai.llm.schemas import MasterDocument, TechStack

    master = MasterDocument(
        project_name="H",
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
    mock_llm = AsyncMock()
    mock_llm.complete.side_effect = [
        __import__("forgeai.llm.schemas", fromlist=["LLMResponse"]).LLMResponse(
            content=json.dumps(
                {
                    "change_type": "BUGFIX",
                    "risk_level": "LOW",
                    "reasoning": "r",
                    "estimated_new_tasks": 0,
                }
            ),
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        ),
        __import__("forgeai.llm.schemas", fromlist=["LLMResponse"]).LLMResponse(
            content=json.dumps(
                {
                    "affected_task_titles": [],
                    "conflicting_task_titles": [],
                    "new_tasks_required": [],
                    "estimated_cost_usd": 0.01,
                    "estimated_time_minutes": 2,
                }
            ),
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        ),
    ]
    lead._llm_client = mock_llm
    entry = await lead.accept_change_request("Fix bug", pid, master, _approve)
    res = await db_session.execute(
        select(ProjectArtefactModel).where(
            ProjectArtefactModel.project_id == pid,
            ProjectArtefactModel.artefact_type == f"change_history:{entry.entry_id}",
        )
    )
    assert res.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_patch_result_task_counts(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_1", db_session, llm_client=AsyncMock())
    reg = ProjectRegistry(db_session)
    project = await reg.create_project("Counts", "brief")
    pid = uuid.UUID(project.id)
    task = await lead.create_task(
        "Endpoint",
        "desc",
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

    from forgeai.lifecycle.schemas import ImpactAnalysis

    impact = ImpactAnalysis(
        project_id=project.id,
        change_request="Fix",
        classification=_classification(),
        affected_task_ids=[str(task.id)],
    )
    approval = HumanChangeApproval(
        project_id=project.id,
        change_request="Fix",
        impact_analysis=impact,
        decision=ChangeDecision.PROCEED,
    )
    loop = LoopCounter()
    ladder = EscalationLadder(loop, EscalationPersistence(db_session))
    patch = PatchExecutor(lead, lead.build_qa_orchestrator(loop, ladder), db_session)
    result = await patch.execute(impact, approval, project.id)
    assert len(result.rework_tasks_completed) == 1


@pytest.mark.asyncio
async def test_project_stays_live_after_patch(db_session: AsyncSession) -> None:
    reg = ProjectRegistry(db_session)
    project = await reg.create_project("Live", "brief")
    await reg.set_live(project.id, "v1")
    lead = LeadAgent("lead_1", db_session, llm_client=AsyncMock())
    from forgeai.lifecycle.schemas import ImpactAnalysis

    impact = ImpactAnalysis(
        project_id=project.id,
        change_request="noop",
        classification=_classification(),
    )
    approval = HumanChangeApproval(
        project_id=project.id,
        change_request="noop",
        impact_analysis=impact,
        decision=ChangeDecision.PROCEED,
    )
    loop = LoopCounter()
    ladder = EscalationLadder(loop, EscalationPersistence(db_session))
    await PatchExecutor(
        lead, lead.build_qa_orchestrator(loop, ladder), db_session
    ).execute(impact, approval, project.id)
    row = await reg.get_project(project.id)
    assert row is not None
    assert row.status == ProjectStatus.LIVE


@pytest.mark.asyncio
async def test_conflicting_in_progress_checkpoint(db_session: AsyncSession) -> None:
    tm = AsyncMock()
    lead = LeadAgent("lead_1", db_session, llm_client=AsyncMock(), task_memory=tm)
    reg = ProjectRegistry(db_session)
    project = await reg.create_project("Pause", "brief")
    pid = uuid.UUID(project.id)
    busy = await lead.create_task(
        "Busy task",
        None,
        TaskComplexity.LOW,
        "backend_agent_1",
        project_id=pid,
    )
    await lead.approve_phase_transition(busy.id)
    sm = TaskStateMachine(db_session)
    await sm.transition(busy.id, TaskState.IN_PROGRESS, "backend_agent_1")

    from forgeai.lifecycle.schemas import ImpactAnalysis

    impact = ImpactAnalysis(
        project_id=project.id,
        change_request="patch",
        classification=_classification(),
        conflicting_task_ids=[str(busy.id)],
    )
    approval = HumanChangeApproval(
        project_id=project.id,
        change_request="patch",
        impact_analysis=impact,
        decision=ChangeDecision.PROCEED,
    )
    loop = LoopCounter()
    ladder = EscalationLadder(loop, EscalationPersistence(db_session))
    await PatchExecutor(
        lead, lead.build_qa_orchestrator(loop, ladder), db_session
    ).execute(impact, approval, project.id)
    tm.set.assert_any_call(str(busy.id), "patch_checkpoint", ANY)


@pytest.mark.asyncio
async def test_regression_runs_for_adjacent_done_tasks(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_1", db_session, llm_client=AsyncMock())
    pid = uuid.uuid4()
    sm = TaskStateMachine(db_session)

    async def _done(title: str) -> str:
        t = await lead.create_task(
            title,
            None,
            TaskComplexity.LOW,
            "backend_agent_1",
            project_id=pid,
        )
        await lead.approve_phase_transition(t.id)
        await sm.transition(t.id, TaskState.IN_PROGRESS, "backend_agent_1")
        await sm.transition(
            t.id,
            TaskState.IN_REVIEW,
            "backend_agent_1",
            **{KEY_WORK_OUTPUT: "c"},
        )
        await sm.transition(t.id, TaskState.TESTING, "qa_1")
        await sm.transition(t.id, TaskState.DONE, "qa_1", **{KEY_OUTPUT: "ok"})
        return str(t.id)

    affected_id = await _done("Affected API")
    await _done("Adjacent API")

    from forgeai.lifecycle.schemas import ImpactAnalysis

    impact = ImpactAnalysis(
        project_id=str(pid),
        change_request="fix affected",
        classification=_classification(),
        affected_task_ids=[affected_id],
    )
    approval = HumanChangeApproval(
        project_id=str(pid),
        change_request="fix",
        impact_analysis=impact,
        decision=ChangeDecision.PROCEED,
    )
    loop = LoopCounter()
    ladder = EscalationLadder(loop, EscalationPersistence(db_session))
    result = await PatchExecutor(
        lead, lead.build_qa_orchestrator(loop, ladder), db_session
    ).execute(impact, approval, str(pid))
    assert result.regression_tests_passed is True
