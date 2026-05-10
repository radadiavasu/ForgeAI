"""Chroma-backed semantic lesson storage per agent role."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import chromadb
from chromadb.api.models.Collection import Collection

from forgeai.memory.schemas import Lesson, LessonQueryResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _collection_name(agent_role: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", agent_role)
    return f"agent_memory_{safe}"


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

    async def write_lesson(self, lesson: Lesson) -> None:
        def _write() -> None:
            coll = self._get_collection(lesson.agent_role)
            doc_id = lesson.id
            text = f"{lesson.failure_description} {lesson.root_cause} {lesson.rule}"
            meta: dict[str, Any] = {
                "id": lesson.id,
                "agent_role": lesson.agent_role,
                "failure_description": lesson.failure_description,
                "root_cause": lesson.root_cause,
                "resolution": lesson.resolution,
                "rule": lesson.rule,
                "created_at": lesson.created_at.isoformat(),
                "project_id": lesson.project_id,
                "task_id": lesson.task_id,
            }
            coll.upsert(ids=[doc_id], documents=[text], metadatas=[meta])

        await asyncio.to_thread(_write)
        preview = lesson.rule[:50] + ("…" if len(lesson.rule) > 50 else "")
        logger.info("Lesson written: %s — %s", lesson.agent_role, preview)

    def _metadata_to_lesson(self, meta: dict[str, Any]) -> Lesson:
        raw_created = meta.get("created_at", "")
        try:
            created = datetime.fromisoformat(str(raw_created))
        except ValueError:
            created = datetime.now(UTC)
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
        )

    async def retrieve_lessons(
        self,
        agent_role: str,
        task_description: str,
        top_k: int = 3,
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
                n_results=min(top_k, max(count, 1)),
                include=["metadatas", "distances"],
            )
            out: list[LessonQueryResult] = []
            metas = res.get("metadatas") or []
            distances = res.get("distances") or []
            if not metas or not metas[0]:
                return []
            for i, meta in enumerate(metas[0]):
                if not meta:
                    continue
                lesson = self._metadata_to_lesson(meta)
                dist = 0.0
                if distances and distances[0] and i < len(distances[0]):
                    dist = float(distances[0][i])
                score = max(0.0, min(1.0, 1.0 - dist / 2.0))
                out.append(LessonQueryResult(lesson=lesson, relevance_score=score))
            return out

        return await asyncio.to_thread(_query)

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
