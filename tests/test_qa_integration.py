"""Integration tests for QAAgent with TestRunner and Sandbox."""

from __future__ import annotations

import docker
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
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
