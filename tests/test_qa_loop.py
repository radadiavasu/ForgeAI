"""QAOrchestrator tests — mocked LLM, no Docker."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.escalation import EscalationLadder, EscalationPersistence
from forgeai.escalation.loop_counter import LoopCounter
from forgeai.llm.schemas import LLMResponse
from forgeai.memory.task_memory import TaskMemory
from forgeai.models.task import Task, TaskComplexity
from forgeai.orchestration.qa_loop import QAOrchestrator
from forgeai.sandbox.schemas import RunnerOutput, SandboxTestCaseResult
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_WORK_OUTPUT


def _defect_json() -> str:
    return json.dumps(
        {
            "failure_summary": "add() returns wrong value",
            "suggestions": "Return a + b instead of a - b",
            "failed_tests": ["test_add"],
            "passed_tests": [],
        }
    )


def _passing_output() -> RunnerOutput:
    return RunnerOutput(
        success=True,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        test_cases=[SandboxTestCaseResult(name="test_add", passed=True)],
        stdout="ok",
        stderr="",
        execution_time_seconds=0.1,
    )


def _failing_output() -> RunnerOutput:
    return RunnerOutput(
        success=False,
        total_tests=1,
        passed_tests=0,
        failed_tests=1,
        test_cases=[
            SandboxTestCaseResult(name="test_add", passed=False, error="assert 1==3")
        ],
        stdout="F",
        stderr="AssertionError",
        execution_time_seconds=0.1,
    )


@pytest.fixture
def task_memory() -> TaskMemory:
    return TaskMemory("redis://localhost:6379/0", ttl_seconds=3600)


@pytest.fixture
def mock_llm() -> AsyncMock:
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(
        content=_defect_json(),
        model_used="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.001,
    )
    return llm


@pytest.fixture
def orchestrator(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    task_memory: TaskMemory,
) -> QAOrchestrator:
    loop_counter = LoopCounter()
    ladder = EscalationLadder(loop_counter, EscalationPersistence(db_session))
    sm = TaskStateMachine(db_session, task_memory=task_memory)
    return QAOrchestrator(
        sm,
        loop_counter,
        ladder,
        mock_llm,
        db_session,
        task_memory=task_memory,
    )


async def _task_at_testing(db_session: AsyncSession, task_memory: TaskMemory) -> Task:
    lead = LeadAgent("lead_1", db_session, task_memory=task_memory)
    backend = BackendAgent("backend_1", db_session, task_memory=task_memory)
    qa = QAAgent("qa_1", db_session, task_memory=task_memory)
    task = await lead.create_task(
        "QA loop task",
        None,
        TaskComplexity.LOW,
        "backend_1",
    )
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    await backend.complete_work(task.id, output="work")
    await qa.begin_review(task.id)
    await db_session.refresh(task)
    assert task.current_state == TaskState.TESTING
    return task


@pytest.mark.asyncio
async def test_process_result_approves_on_success(
    db_session: AsyncSession,
    orchestrator: QAOrchestrator,
    task_memory: TaskMemory,
) -> None:
    task = await _task_at_testing(db_session, task_memory)
    decision = await orchestrator.process_result(
        str(task.id),
        _passing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    assert decision.approved is True
    assert decision.defect_report is None
    await db_session.refresh(task)
    assert task.current_state == TaskState.DONE


@pytest.mark.asyncio
async def test_process_result_rejects_on_failure(
    db_session: AsyncSession,
    orchestrator: QAOrchestrator,
    task_memory: TaskMemory,
) -> None:
    task = await _task_at_testing(db_session, task_memory)
    decision = await orchestrator.process_result(
        str(task.id),
        _failing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    assert decision.approved is False
    assert decision.defect_report is not None
    await db_session.refresh(task)
    assert task.current_state == TaskState.IN_PROGRESS


@pytest.mark.asyncio
async def test_defect_report_generated_on_rejection(
    db_session: AsyncSession,
    orchestrator: QAOrchestrator,
    task_memory: TaskMemory,
    mock_llm: AsyncMock,
) -> None:
    task = await _task_at_testing(db_session, task_memory)
    decision = await orchestrator.process_result(
        str(task.id),
        _failing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    assert decision.defect_report is not None
    assert decision.defect_report.failure_summary
    assert decision.defect_report.original_agent_id == "backend_1"
    mock_llm.complete.assert_awaited()
    assert mock_llm.complete.await_args.kwargs.get("complexity") == "LOW"


@pytest.mark.asyncio
async def test_defect_report_stored_in_task_memory(
    db_session: AsyncSession,
    orchestrator: QAOrchestrator,
    task_memory: TaskMemory,
) -> None:
    task = await _task_at_testing(db_session, task_memory)
    await orchestrator.process_result(
        str(task.id),
        _failing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    stored = await task_memory.get(str(task.id), "defect_report")
    assert stored is not None
    payload = json.loads(stored)
    assert payload["original_agent_id"] == "backend_1"


@pytest.mark.asyncio
async def test_loop_counter_incremented_on_rejection(
    db_session: AsyncSession,
    orchestrator: QAOrchestrator,
    task_memory: TaskMemory,
) -> None:
    task = await _task_at_testing(db_session, task_memory)
    await orchestrator.process_result(
        str(task.id),
        _failing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    assert await orchestrator.loop_counter.get(str(task.id), "qa_failure") == 1


@pytest.mark.asyncio
async def test_loop_counter_reset_on_approval(
    db_session: AsyncSession,
    orchestrator: QAOrchestrator,
    task_memory: TaskMemory,
) -> None:
    task = await _task_at_testing(db_session, task_memory)
    await orchestrator.loop_counter.increment(str(task.id), "qa_failure")
    await orchestrator.process_result(
        str(task.id),
        _passing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    assert await orchestrator.loop_counter.get(str(task.id), "qa_failure") == 0


@pytest.mark.asyncio
async def test_escalation_when_loop_counter_reaches_three(
    db_session: AsyncSession,
    orchestrator: QAOrchestrator,
    task_memory: TaskMemory,
) -> None:
    task = await _task_at_testing(db_session, task_memory)
    for _ in range(3):
        await orchestrator.loop_counter.increment(str(task.id), "qa_failure")
    decision = await orchestrator.process_result(
        str(task.id),
        _failing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    assert decision.escalated is True
    assert decision.escalation_result is not None


@pytest.mark.asyncio
async def test_reassignment_targets_original_agent(
    db_session: AsyncSession,
    orchestrator: QAOrchestrator,
    task_memory: TaskMemory,
) -> None:
    task = await _task_at_testing(db_session, task_memory)
    decision = await orchestrator.process_result(
        str(task.id),
        _failing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    assert decision.defect_report is not None
    assert decision.defect_report.original_agent_id == "backend_1"
    assert decision.defect_report.agent_id == "qa_1"


@pytest.mark.asyncio
async def test_approval_transition_testing_to_done(
    db_session: AsyncSession,
    orchestrator: QAOrchestrator,
    task_memory: TaskMemory,
) -> None:
    task = await _task_at_testing(db_session, task_memory)
    await orchestrator.process_result(
        str(task.id),
        _passing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    result = await db_session.execute(select(Task).where(Task.id == task.id))
    assert result.scalar_one().current_state == TaskState.DONE


@pytest.mark.asyncio
async def test_rejection_transition_testing_to_in_progress(
    db_session: AsyncSession,
    orchestrator: QAOrchestrator,
    task_memory: TaskMemory,
) -> None:
    task = await _task_at_testing(db_session, task_memory)
    await orchestrator.process_result(
        str(task.id),
        _failing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    result = await db_session.execute(select(Task).where(Task.id == task.id))
    assert result.scalar_one().current_state == TaskState.IN_PROGRESS


@pytest.mark.asyncio
async def test_qa_decision_defect_populated_only_on_rejection(
    db_session: AsyncSession,
    orchestrator: QAOrchestrator,
    task_memory: TaskMemory,
) -> None:
    task = await _task_at_testing(db_session, task_memory)
    ok = await orchestrator.process_result(
        str(task.id),
        _passing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    assert ok.defect_report is None

    task2 = await _task_at_testing(db_session, task_memory)
    bad = await orchestrator.process_result(
        str(task2.id),
        _failing_output(),
        "qa_1",
        "backend_1",
        "BACKEND_PHASE",
    )
    assert bad.defect_report is not None
