"""Lesson health scoring, flagging, and context guard helpers (Phase 9)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from forgeai.llm.schemas import TechStackDocument
from forgeai.memory.schemas import Lesson

if TYPE_CHECKING:
    from forgeai.memory.agent_memory import AgentMemory

FLAG_THRESHOLD = 0.5
UNFLAG_THRESHOLD = 0.7


def confidence_from_escalation_level(level: int, human_verified: bool) -> str:
    if human_verified:
        return "high"
    if level <= 2:
        return "low"
    if level == 3:
        return "medium"
    return "high"


def build_context_guards(tech_stack: TechStackDocument) -> dict[str, str]:
    return {
        "language": tech_stack.language,
        "framework": tech_stack.framework,
        "database": tech_stack.database,
        "environment": "any",
    }


def context_matches(guards: dict[str, str], current_context: dict[str, str]) -> bool:
    for key, value in guards.items():
        if value == "any":
            continue
        if current_context.get(key) != value:
            return False
    return True


def recalculate_health_score(lesson: Lesson) -> float:
    if lesson.total_uses <= 0:
        return 1.0
    return lesson.success_count / lesson.total_uses


class LessonHealth:
    """Track lesson usage outcomes and manage flagging."""

    def __init__(self, agent_memory: AgentMemory) -> None:
        self.memory = agent_memory

    async def record_success(self, lesson_id: str, agent_role: str) -> None:
        lesson = await self.memory.get_lesson(agent_role, lesson_id)
        if lesson is None:
            return
        lesson.total_uses += 1
        lesson.success_count += 1
        lesson.health_score = recalculate_health_score(lesson)
        if lesson.flagged and lesson.health_score >= UNFLAG_THRESHOLD:
            lesson.flagged = False
            lesson.flag_reason = ""
        await self.memory.write_lesson(lesson)

    async def record_failure(
        self,
        lesson_id: str,
        agent_role: str,
        failure_detail: str,
    ) -> None:
        lesson = await self.memory.get_lesson(agent_role, lesson_id)
        if lesson is None:
            return
        lesson.total_uses += 1
        lesson.fail_count += 1
        lesson.health_score = recalculate_health_score(lesson)
        if lesson.health_score < FLAG_THRESHOLD:
            lesson.flagged = True
            lesson.flag_reason = failure_detail
        await self.memory.write_lesson(lesson)

    async def flag_lesson(self, lesson_id: str, agent_role: str, reason: str) -> None:
        lesson = await self.memory.get_lesson(agent_role, lesson_id)
        if lesson is None:
            return
        lesson.flagged = True
        lesson.flag_reason = reason
        await self.memory.write_lesson(lesson)

    async def get_health_report(self, agent_role: str) -> list[dict[str, Any]]:
        lessons = await self.memory.list_lessons(agent_role)
        rows = [
            {
                "lesson_id": les.id,
                "rule": les.rule,
                "health_score": les.health_score,
                "total_uses": les.total_uses,
                "success_count": les.success_count,
                "fail_count": les.fail_count,
                "flagged": les.flagged,
                "confidence": les.confidence,
            }
            for les in lessons
        ]
        return sorted(rows, key=lambda r: r["health_score"])
