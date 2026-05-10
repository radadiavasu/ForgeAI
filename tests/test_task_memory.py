"""Tests for TaskMemory (Redis)."""

from __future__ import annotations

import asyncio

import pytest

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.memory.task_memory import TaskMemory
from forgeai.models.task import TaskComplexity
from forgeai.state_machine.states import TaskState


@pytest.mark.asyncio
async def test_set_get_round_trip() -> None:
    tm = TaskMemory("redis://localhost:6379", ttl_seconds=3600)
    await tm.set("t1", "k", "v")
    assert await tm.get("t1", "k") == "v"


@pytest.mark.asyncio
async def test_get_missing_returns_none() -> None:
    tm = TaskMemory("redis://localhost:6379")
    assert await tm.get("missing-task", "missing-key") is None


@pytest.mark.asyncio
async def test_delete_all_removes_keys_for_task() -> None:
    tm = TaskMemory("redis://localhost:6379")
    await tm.set("ta", "a", "1")
    await tm.set("ta", "b", "2")
    await tm.delete_all("ta")
    assert await tm.get("ta", "a") is None
    assert await tm.get("ta", "b") is None


@pytest.mark.asyncio
async def test_delete_all_does_not_touch_other_tasks() -> None:
    tm = TaskMemory("redis://localhost:6379")
    await tm.set("ta", "k", "x")
    await tm.set("tb", "k", "y")
    await tm.delete_all("ta")
    assert await tm.get("tb", "k") == "y"


@pytest.mark.asyncio
async def test_exists_true_false() -> None:
    tm = TaskMemory("redis://localhost:6379")
    await tm.set("tc", "k", "v")
    assert await tm.exists("tc", "k") is True
    assert await tm.exists("tc", "missing") is False


@pytest.mark.asyncio
async def test_ttl_expiry() -> None:
    tm = TaskMemory("redis://localhost:6379", ttl_seconds=1)
    await tm.set("ttl-task", "k", "v")
    assert await tm.get("ttl-task", "k") == "v"
    await asyncio.sleep(2.1)
    assert await tm.get("ttl-task", "k") is None


@pytest.mark.asyncio
async def test_cleared_when_task_done(db_session) -> None:
    tm = TaskMemory("redis://localhost:6379")
    lead = LeadAgent("lead_sm", db_session, task_memory=tm)
    backend = BackendAgent("backend_sm", db_session, task_memory=tm)
    qa = QAAgent("qa_sm", db_session, task_memory=tm)
    task = await lead.create_task("tm demo", None, TaskComplexity.LOW, "backend_sm")
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    tid = str(task.id)
    await tm.set(tid, "note", "hello")
    await backend.complete_work(task.id, output="out")
    await qa.begin_review(task.id)
    await qa.approve(task.id, output="out")
    await db_session.refresh(task)
    assert task.current_state == TaskState.DONE
    assert await tm.get(tid, "note") is None
