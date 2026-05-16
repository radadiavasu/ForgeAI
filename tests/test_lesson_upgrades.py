"""Phase 9 Agent_Memory upgrades — Chroma in-process, no real LLM."""

from __future__ import annotations

from datetime import UTC, datetime

import chromadb
import pytest

from forgeai.memory.agent_memory import LESSON_COMPATIBILITY_PROMPT, AgentMemory, new_lesson_id
from forgeai.memory.lesson_health import (
    LessonHealth,
    build_context_guards,
    confidence_from_escalation_level,
    context_matches,
)
from forgeai.memory.schemas import Lesson
from forgeai.llm.schemas import TechStackDocument


def _lesson(**kwargs) -> Lesson:
    base = dict(
        id=new_lesson_id(),
        agent_role="backend_agent",
        failure_description="f",
        root_cause="r",
        resolution="x",
        rule="validate input",
        created_at=datetime.now(UTC),
        project_id="00000000-0000-0000-0000-000000000001",
        task_id="00000000-0000-0000-0000-000000000002",
    )
    base.update(kwargs)
    return Lesson(**base)


@pytest.fixture
def memory(tmp_path_factory: pytest.TempPathFactory) -> AgentMemory:
    client = chromadb.PersistentClient(path=str(tmp_path_factory.mktemp("chroma")))
    return AgentMemory("localhost", 8000, chroma_client=client)


def test_confidence_from_escalation_level_mapping() -> None:
    assert confidence_from_escalation_level(1, False) == "low"
    assert confidence_from_escalation_level(2, False) == "low"
    assert confidence_from_escalation_level(3, False) == "medium"
    assert confidence_from_escalation_level(4, False) == "high"
    assert confidence_from_escalation_level(5, False) == "high"


def test_human_verified_always_high() -> None:
    assert confidence_from_escalation_level(1, True) == "high"


@pytest.mark.asyncio
async def test_lesson_written_with_confidence_field(memory: AgentMemory) -> None:
    les = _lesson(confidence="medium", resolved_at_escalation_level=3)
    await memory.write_lesson(les)
    loaded = await memory.get_lesson("backend_agent", les.id)
    assert loaded is not None
    assert loaded.confidence == "medium"


@pytest.mark.asyncio
async def test_record_success_updates_health(memory: AgentMemory) -> None:
    les = _lesson()
    await memory.write_lesson(les)
    health = LessonHealth(memory)
    await health.record_success(les.id, les.agent_role)
    await health.record_success(les.id, les.agent_role)
    updated = await memory.get_lesson(les.agent_role, les.id)
    assert updated is not None
    assert updated.success_count == 2
    assert updated.total_uses == 2
    assert updated.health_score == 1.0


@pytest.mark.asyncio
async def test_record_failure_recalculates_health(memory: AgentMemory) -> None:
    les = _lesson()
    await memory.write_lesson(les)
    health = LessonHealth(memory)
    await health.record_success(les.id, les.agent_role)
    await health.record_failure(les.id, les.agent_role, "still failing")
    updated = await memory.get_lesson(les.agent_role, les.id)
    assert updated is not None
    assert updated.fail_count == 1
    assert updated.health_score == 0.5


@pytest.mark.asyncio
async def test_auto_flag_below_half_health(memory: AgentMemory) -> None:
    les = _lesson()
    await memory.write_lesson(les)
    health = LessonHealth(memory)
    await health.record_failure(les.id, les.agent_role, "bad")
    updated = await memory.get_lesson(les.agent_role, les.id)
    assert updated is not None
    assert updated.flagged is True


@pytest.mark.asyncio
async def test_auto_unflag_above_threshold(memory: AgentMemory) -> None:
    les = _lesson(flagged=True, flag_reason="old", health_score=0.4, total_uses=2, success_count=1)
    await memory.write_lesson(les)
    health = LessonHealth(memory)
    for _ in range(4):
        await health.record_success(les.id, les.agent_role)
    updated = await memory.get_lesson(les.agent_role, les.id)
    assert updated is not None
    assert updated.flagged is False


@pytest.mark.asyncio
async def test_flagged_lessons_excluded_from_retrieve(memory: AgentMemory) -> None:
    les = _lesson(rule="flagged rule unique xyz", flagged=True)
    await memory.write_lesson(les)
    out = await memory.retrieve_lessons("backend_agent", "flagged rule unique xyz", top_k=3)
    assert all(r.lesson.id != les.id for r in out)


def test_context_matches_all_guards() -> None:
    guards = {"framework": "React", "language": "Python"}
    assert context_matches(guards, {"framework": "React", "language": "Python"}) is True


def test_context_matches_mismatch() -> None:
    guards = {"framework": "Django"}
    assert context_matches(guards, {"framework": "React"}) is False


def test_context_matches_any_skips() -> None:
    guards = {"framework": "React", "environment": "any"}
    assert context_matches(guards, {"framework": "React", "environment": "prod"}) is True


@pytest.mark.asyncio
async def test_retrieve_filters_by_context_guards(memory: AgentMemory) -> None:
    les = _lesson(
        rule="django orm lesson unique abc",
        context_guards={"framework": "Django", "language": "Python"},
    )
    await memory.write_lesson(les)
    out = await memory.retrieve_lessons(
        "backend_agent",
        "django orm lesson",
        top_k=3,
        current_context={"framework": "React", "language": "Python"},
    )
    assert all(r.lesson.id != les.id for r in out)


def test_build_context_guards_from_tech_stack() -> None:
    ts = TechStackDocument(
        language="Python",
        framework="FastAPI",
        database="PostgreSQL",
        testing_framework="pytest",
        rationale="r",
    )
    guards = build_context_guards(ts)
    assert guards["framework"] == "FastAPI"
    assert guards["environment"] == "any"


@pytest.mark.asyncio
async def test_format_lessons_for_prompt_labels(memory: AgentMemory) -> None:
    les = _lesson(confidence="low", rule="retry on timeout")
    await memory.write_lesson(les)
    ranked = await memory.retrieve_lessons("backend_agent", "retry timeout", top_k=1)
    text = memory.format_lessons_for_prompt(
        ranked,
        "Build API",
        "Task manager",
        "Python FastAPI",
    )
    assert text
    assert "LOW CONFIDENCE" in text
    assert "Before starting" in text
    assert "APPLY" in text
