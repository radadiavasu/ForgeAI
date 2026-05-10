"""Integration tests for QAAgent with TestRunner and Sandbox."""

from __future__ import annotations

import docker
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.escalation import (
    EscalationLadder,
    EscalationLevel,
    EscalationPersistence,
    LoopCounter,
)
from forgeai.exceptions import SelfApprovalError
from forgeai.models.task import Task, TaskComplexity
from forgeai.sandbox.runner import TestRunner
from forgeai.sandbox.sandbox import Sandbox, SandboxConfig
from forgeai.state_machine.states import TaskState


def _docker_available() -> bool:
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(), reason="Docker daemon unavailable for QA integration tests"
)


def _sandbox(complexity: str) -> Sandbox:
    return Sandbox(
        complexity=complexity,
        config=SandboxConfig(
            image="python:3.11-slim",
            cpu_limit=1.0,
            memory_limit="256m",
            timeout_low=20,
            timeout_medium=45,
            timeout_high=120,
            working_dir="/sandbox",
        ),
    )


@pytest.mark.asyncio
async def test_full_cycle_to_done(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_agent_1", db_session)
    backend = BackendAgent("backend_agent_1", db_session)
    qa = QAAgent("qa_agent_1", db_session, test_runner=TestRunner(_sandbox("MEDIUM")))

    task = await lead.create_task("Build Auth API", None, TaskComplexity.MEDIUM, "backend_agent_1")
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    await backend.complete_work(task.id, output="JWT auth implemented")
    await qa.begin_review(task.id)
    output = await qa.review(
        task.id,
        code="def add(a,b):\n    return a+b\n",
        test_code="from main import add\n\ndef test_add():\n    assert add(1,2) == 3\n",
    )
    if output.success:
        await qa.approve(task.id, output="JWT auth implemented")
    result = await db_session.execute(select(Task).where(Task.id == task.id))
    updated = result.scalar_one()
    assert updated.current_state == TaskState.DONE


@pytest.mark.asyncio
async def test_rejection_cycle_back_to_in_progress(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_agent_1", db_session)
    backend = BackendAgent("backend_agent_1", db_session)
    qa = QAAgent("qa_agent_1", db_session, test_runner=TestRunner(_sandbox("LOW")))

    task = await lead.create_task("Failing task", None, TaskComplexity.LOW, "backend_agent_1")
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    await backend.complete_work(task.id, output="buggy implementation")
    await qa.begin_review(task.id)
    output = await qa.review(
        task.id,
        code="def add(a,b):\n    return a-b\n",
        test_code="from main import add\n\ndef test_add():\n    assert add(1,2) == 3\n",
    )
    assert output.success is False
    await qa.reject(task.id, defect_report="add() returns wrong value")
    result = await db_session.execute(select(Task).where(Task.id == task.id))
    updated = result.scalar_one()
    assert updated.current_state == TaskState.IN_PROGRESS


@pytest.mark.asyncio
async def test_self_approval_review_blocked_state_unchanged(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_agent_1", db_session)
    backend = BackendAgent("same_agent", db_session)
    qa = QAAgent("same_agent", db_session, test_runner=TestRunner(_sandbox("LOW")))

    task = await lead.create_task("Self review", None, TaskComplexity.LOW, "same_agent")
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    await backend.complete_work(task.id, output="impl")
    await qa.begin_review(task.id)

    with pytest.raises(SelfApprovalError):
        await qa.review(
            task.id,
            code="def noop():\n    return True\n",
            test_code="def test_noop():\n    assert True\n",
        )
    result = await db_session.execute(select(Task).where(Task.id == task.id))
    updated = result.scalar_one()
    assert updated.current_state == TaskState.TESTING


@pytest.mark.asyncio
async def test_output_populated_when_done(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_agent_1", db_session)
    backend = BackendAgent("backend_agent_1", db_session)
    qa = QAAgent("qa_agent_1", db_session, test_runner=TestRunner(_sandbox("LOW")))

    task = await lead.create_task("Output task", None, TaskComplexity.LOW, "backend_agent_1")
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    await backend.complete_work(task.id, output="impl output")
    await qa.begin_review(task.id)
    output = await qa.review(
        task.id,
        code="def ok():\n    return True\n",
        test_code="from main import ok\n\ndef test_ok():\n    assert ok() is True\n",
    )
    assert output.success
    await qa.approve(task.id, output="impl output")
    result = await db_session.execute(select(Task).where(Task.id == task.id))
    updated = result.scalar_one()
    assert updated.output == "impl output"


@pytest.mark.asyncio
async def test_failing_tests_trigger_escalation(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_agent_1", db_session)
    backend = BackendAgent("backend_agent_1", db_session)
    qa = QAAgent("qa_agent_1", db_session, test_runner=TestRunner(_sandbox("HIGH")))
    ladder = EscalationLadder(
        loop_counter=LoopCounter(),
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )

    task = await lead.create_task("Escalation task", None, TaskComplexity.HIGH, "backend_agent_1")
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    await backend.complete_work(task.id, output="broken payment impl")
    await qa.begin_review(task.id)
    output = await qa.review(
        task.id,
        code="def process_payment(amount):\n    return {}\n",
        test_code=(
            "from main import process_payment\n\n"
            "def test_payment_has_status():\n"
            "    assert process_payment(1).get('status') == 'success'\n"
        ),
    )
    assert output.success is False
    result = await ladder.escalate(
        task_id=str(task.id),
        agent_id=lead.agent_id,
        error_signature="test_failure:assertion_error",
        error_detail="assertion failure in payment tests",
        task_specification="Build Payment API",
    )
    assert result.needs_human_input is True


@pytest.mark.asyncio
async def test_escalation_result_logged_correctly(db_session: AsyncSession) -> None:
    lead = LeadAgent("lead_agent_1", db_session)
    ladder = EscalationLadder(
        loop_counter=LoopCounter(),
        persistence=EscalationPersistence(db_session),
        max_self_retries=2,
    )
    log_task_id = "11111111-1111-1111-1111-222222222222"
    result = await ladder.escalate(
        task_id=log_task_id,
        agent_id=lead.agent_id,
        error_signature="sandbox_timeout",
        error_detail="sandbox timed out",
        task_specification="Build resilient timeout handling",
    )

    events = await ladder.get_events(log_task_id)
    assert result.level_reached == EscalationLevel.HUMAN_INPUT
    assert len(events) >= 5
    assert events[-1].level == EscalationLevel.HUMAN_INPUT
