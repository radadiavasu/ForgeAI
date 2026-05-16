"""Chroma-backed semantic lesson storage per agent role."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import chromadb
from chromadb.api.models.Collection import Collection

from forgeai.memory.lesson_health import context_matches
from forgeai.memory.schemas import Lesson, LessonQueryResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

LESSON_COMPATIBILITY_PROMPT = """
Before starting your task, you have been given {count} relevant
lesson(s) from past failures on similar tasks:

{lessons_formatted}

For each lesson, do the following:
1. Compare the lesson against your project context:
   - Master Document: {master_doc_summary}
   - Tech Stack: {tech_stack_summary}
   - Your task: {task_description}

2. Decide:
   APPLY   — lesson is fully compatible. Follow it as your
             first approach.
   ADAPT   — lesson intent is right but specifics differ.
             Use its approach, adjust to your tech stack.
   IGNORE  — lesson contradicts your project context.
             Proceed independently.

3. State your decision and reasoning before starting.

Project docs always take priority over lessons.
A lesson is a shortcut only when it aligns with your context.
""".strip()


def _collection_name(agent_role: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", agent_role)
    return f"agent_memory_{safe}"


def _confidence_label(confidence: str) -> str:
    if confidence == "high":
        return "[HIGH CONFIDENCE]"
    if confidence == "medium":
        return "[MEDIUM CONFIDENCE — verify applies]"
    return "[LOW CONFIDENCE — hint only]"


class AgentMemory:
    """Stores and queries Lessons in Chroma collections keyed by agent_role."""

    def __init__(
        self,
        chroma_host: str,
        chroma_port: int,
        *,
        chroma_client: chromadb.ClientAPI | None = None,
    ) -> None:
        self._host = chroma_host
        self._port = chroma_port
        self._client: chromadb.ClientAPI = chroma_client or chromadb.HttpClient(
            host=chroma_host,
            port=chroma_port,
        )

    def _get_collection(self, agent_role: str) -> Collection:
        name = _collection_name(agent_role)
        return self._client.get_or_create_collection(
            name=name,
            metadata={"agent_role": agent_role},
        )

    def _lesson_to_metadata(self, lesson: Lesson) -> dict[str, Any]:
        return {
            "id": lesson.id,
            "agent_role": lesson.agent_role,
            "failure_description": lesson.failure_description,
            "root_cause": lesson.root_cause,
            "resolution": lesson.resolution,
            "rule": lesson.rule,
            "created_at": lesson.created_at.isoformat(),
            "project_id": lesson.project_id,
            "task_id": lesson.task_id,
            "confidence": lesson.confidence,
            "human_verified": lesson.human_verified,
            "resolved_at_escalation_level": lesson.resolved_at_escalation_level,
            "health_score": float(lesson.health_score),
            "total_uses": lesson.total_uses,
            "success_count": lesson.success_count,
            "fail_count": lesson.fail_count,
            "flagged": lesson.flagged,
            "flag_reason": lesson.flag_reason or "",
            "context_guards": json.dumps(lesson.context_guards or {}),
            "supersedes": lesson.supersedes or "",
        }

    def _metadata_to_lesson(self, meta: dict[str, Any]) -> Lesson:
        raw_created = meta.get("created_at", "")
        try:
            created = datetime.fromisoformat(str(raw_created))
        except ValueError:
            created = datetime.now(UTC)
        guards_raw = meta.get("context_guards", "{}")
        try:
            guards = json.loads(str(guards_raw)) if guards_raw else {}
        except json.JSONDecodeError:
            guards = {}
        if not isinstance(guards, dict):
            guards = {}
        guards = {str(k): str(v) for k, v in guards.items()}
        sup = meta.get("supersedes", "")
        return Lesson(
            id=str(meta["id"]),
            agent_role=str(meta["agent_role"]),
            failure_description=str(meta["failure_description"]),
            root_cause=str(meta["root_cause"]),
            resolution=str(meta["resolution"]),
            rule=str(meta["rule"]),
            created_at=created,
            project_id=str(meta["project_id"]),
            task_id=str(meta["task_id"]),
            confidence=str(meta.get("confidence", "high")),
            human_verified=bool(meta.get("human_verified", False)),
            resolved_at_escalation_level=int(meta.get("resolved_at_escalation_level", 4)),
            health_score=float(meta.get("health_score", 1.0)),
            total_uses=int(meta.get("total_uses", 0)),
            success_count=int(meta.get("success_count", 0)),
            fail_count=int(meta.get("fail_count", 0)),
            flagged=bool(meta.get("flagged", False)),
            flag_reason=str(meta.get("flag_reason", "")),
            context_guards=guards,
            supersedes=str(sup) if sup else None,
        )

    async def write_lesson(self, lesson: Lesson) -> None:
        def _write() -> None:
            coll = self._get_collection(lesson.agent_role)
            doc_id = lesson.id
            text = f"{lesson.failure_description} {lesson.root_cause} {lesson.rule}"
            coll.upsert(
                ids=[doc_id],
                documents=[text],
                metadatas=[self._lesson_to_metadata(lesson)],
            )

        await asyncio.to_thread(_write)
        preview = lesson.rule[:50] + ("…" if len(lesson.rule) > 50 else "")
        logger.info(
            "Lesson written: %s — %s (confidence=%s)",
            lesson.agent_role,
            preview,
            lesson.confidence,
        )

    async def get_lesson(self, agent_role: str, lesson_id: str) -> Lesson | None:
        def _get() -> Lesson | None:
            try:
                coll = self._get_collection(agent_role)
                res = coll.get(ids=[lesson_id], include=["metadatas"])
                metas = res.get("metadatas") or []
                if not metas or not metas[0]:
                    return None
                return self._metadata_to_lesson(metas[0])
            except Exception:
                return None

        return await asyncio.to_thread(_get)

    async def list_lessons(self, agent_role: str) -> list[Lesson]:
        def _list() -> list[Lesson]:
            try:
                coll = self._get_collection(agent_role)
                res = coll.get(include=["metadatas"])
                metas = res.get("metadatas") or []
                return [self._metadata_to_lesson(m) for m in metas if m]
            except Exception:
                return []

        return await asyncio.to_thread(_list)

    async def retrieve_lessons(
        self,
        agent_role: str,
        task_description: str,
        top_k: int = 3,
        *,
        current_context: dict[str, str] | None = None,
    ) -> list[LessonQueryResult]:
        def _query() -> list[LessonQueryResult]:
            name = _collection_name(agent_role)
            try:
                coll = self._client.get_collection(name=name)
            except Exception:
                return []
            count = coll.count()
            if count == 0:
                return []
            res = coll.query(
                query_texts=[task_description],
                n_results=min(top_k * 3, max(count, 1)),
                include=["metadatas", "distances"],
            )
            out: list[LessonQueryResult] = []
            metas = res.get("metadatas") or []
            distances = res.get("distances") or []
            if not metas or not metas[0]:
                return []
            ctx = current_context or {}
            for i, meta in enumerate(metas[0]):
                if not meta:
                    continue
                lesson = self._metadata_to_lesson(meta)
                if lesson.flagged:
                    continue
                if lesson.context_guards and not context_matches(lesson.context_guards, ctx):
                    continue
                dist = 0.0
                if distances and distances[0] and i < len(distances[0]):
                    dist = float(distances[0][i])
                score = max(0.0, min(1.0, 1.0 - dist / 2.0))
                out.append(LessonQueryResult(lesson=lesson, relevance_score=score))
                if len(out) >= top_k:
                    break
            return out

        return await asyncio.to_thread(_query)

    def format_lessons_for_prompt(
        self,
        lessons: list[LessonQueryResult],
        task_description: str,
        master_doc_summary: str,
        tech_stack_summary: str,
    ) -> str:
        if not lessons:
            return ""
        lines: list[str] = []
        for i, item in enumerate(lessons, start=1):
            label = _confidence_label(item.lesson.confidence)
            lines.append(f"{i}. {label} {item.lesson.rule}")
        lessons_formatted = "\n".join(lines)
        return LESSON_COMPATIBILITY_PROMPT.format(
            count=len(lessons),
            lessons_formatted=lessons_formatted,
            master_doc_summary=master_doc_summary,
            tech_stack_summary=tech_stack_summary,
            task_description=task_description,
        )

    async def get_lesson_count(self, agent_role: str) -> int:
        def _count() -> int:
            name = _collection_name(agent_role)
            try:
                coll = self._client.get_collection(name=name)
                return coll.count()
            except Exception:
                return 0

        return await asyncio.to_thread(_count)


def new_lesson_id() -> str:
    """Generate a new lesson UUID string."""
    return str(uuid.uuid4())
