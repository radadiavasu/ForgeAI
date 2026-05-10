"""Tests for AgentMemory (Chroma)."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime

import chromadb
import pytest

from forgeai.memory.agent_memory import AgentMemory, new_lesson_id
from forgeai.memory.schemas import Lesson


def _make_lesson(
    role: str,
    failure: str,
    root: str,
    res: str,
    rule: str,
    project_id: str,
    task_id: str,
) -> Lesson:
    return Lesson(
        id=new_lesson_id(),
        agent_role=role,
        failure_description=failure,
        root_cause=root,
        resolution=res,
        rule=rule,
        created_at=datetime.now(UTC),
        project_id=project_id,
        task_id=task_id,
    )


@pytest.fixture
def agent_memory(tmp_path_factory: pytest.TempPathFactory) -> AgentMemory:
    path = tmp_path_factory.mktemp("chroma")
    client = chromadb.PersistentClient(path=str(path))
    return AgentMemory("localhost", 8000, chroma_client=client)


@pytest.mark.asyncio
async def test_write_lesson_stores(agent_memory: AgentMemory) -> None:
    lesson = _make_lesson(
        "backend_agent",
        "f",
        "r",
        "x",
        "rule",
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    )
    await agent_memory.write_lesson(lesson)
    assert await agent_memory.get_lesson_count("backend_agent") == 1


@pytest.mark.asyncio
async def test_retrieve_empty_collection(agent_memory: AgentMemory) -> None:
    assert await agent_memory.retrieve_lessons("backend_agent", "anything", top_k=3) == []


@pytest.mark.asyncio
async def test_retrieve_after_writes(agent_memory: AgentMemory) -> None:
    pid = "00000000-0000-0000-0000-000000000010"
    await agent_memory.write_lesson(
        _make_lesson(
            "backend_agent",
            "failure text",
            "cause",
            "fix",
            "my rule",
            pid,
            "00000000-0000-0000-0000-000000000011",
        )
    )
    out = await agent_memory.retrieve_lessons("backend_agent", "failure", top_k=2)
    assert len(out) >= 1
    assert out[0].lesson.rule == "my rule"


@pytest.mark.asyncio
async def test_semantic_ranking_dates_first(agent_memory: AgentMemory) -> None:
    pid = "00000000-0000-0000-0000-000000000020"
    lessons = [
        _make_lesson(
            "backend_agent",
            "Booking API threw errors on date inputs",
            "Timezone inconsistent UTC vs local",
            "fixed",
            "Always convert all dates to UTC at the API boundary",
            pid,
            "00000000-0000-0000-0000-000000000021",
        ),
        _make_lesson(
            "backend_agent",
            "Payment failure",
            "no idempotency",
            "fix",
            "Use idempotency keys",
            pid,
            "00000000-0000-0000-0000-000000000022",
        ),
    ]
    for les in lessons:
        await agent_memory.write_lesson(les)
    ranked = await agent_memory.retrieve_lessons(
        "backend_agent",
        "reservation API booking dates timezones",
        top_k=2,
    )
    assert ranked[0].lesson.rule.startswith("Always convert")


@pytest.mark.asyncio
async def test_scoped_by_agent_role(agent_memory: AgentMemory) -> None:
    pid = "00000000-0000-0000-0000-000000000030"
    await agent_memory.write_lesson(
        _make_lesson(
            "backend_agent",
            "backend bug",
            "c",
            "f",
            "backend rule",
            pid,
            "00000000-0000-0000-0000-000000000031",
        )
    )
    qa_out = await agent_memory.retrieve_lessons("qa_agent", "backend bug", top_k=3)
    assert qa_out == []


@pytest.mark.asyncio
async def test_lesson_count(agent_memory: AgentMemory) -> None:
    pid = "00000000-0000-0000-0000-000000000040"
    assert await agent_memory.get_lesson_count("backend_agent") == 0
    await agent_memory.write_lesson(
        _make_lesson(
            "backend_agent",
            "a",
            "b",
            "c",
            "r",
            pid,
            "00000000-0000-0000-0000-000000000041",
        )
    )
    await agent_memory.write_lesson(
        _make_lesson(
            "backend_agent",
            "a2",
            "b2",
            "c2",
            "r2",
            pid,
            "00000000-0000-0000-0000-000000000042",
        )
    )
    assert await agent_memory.get_lesson_count("backend_agent") == 2


@pytest.mark.asyncio
async def test_same_role_shared_collection(agent_memory: AgentMemory) -> None:
    """Different logical instances share lessons when agent_role matches."""
    pid = "00000000-0000-0000-0000-000000000050"
    lesson = _make_lesson(
        "backend_agent",
        "shared failure",
        "rc",
        "res",
        "shared rule",
        pid,
        "00000000-0000-0000-0000-000000000051",
    )
    await agent_memory.write_lesson(lesson)
    results = await agent_memory.retrieve_lessons("backend_agent", "shared failure", top_k=1)
    assert len(results) == 1
    assert results[0].lesson.rule == "shared rule"
