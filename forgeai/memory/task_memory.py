"""Ephemeral per-task context in Redis with TTL."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import redis.asyncio as redis

from forgeai.config import get_settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class TaskMemory:
    """Redis-backed key-value store scoped per task with TTL on every write."""

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int = 86400,
        *,
        redis_client: redis.Redis | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._ttl_seconds = ttl_seconds
        self._redis: redis.Redis | None = redis_client
        self._owned_client = redis_client is None

    def _key(self, task_id: str, key: str) -> str:
        return f"task_memory:{task_id}:{key}"

    async def _conn(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    @classmethod
    def from_settings(cls) -> TaskMemory:
        s = get_settings()
        return cls(s.redis_url, ttl_seconds=s.task_memory_ttl)

    async def set(self, task_id: str, key: str, value: str) -> None:
        r = await self._conn()
        k = self._key(task_id, key)
        await r.set(k, value, ex=self._ttl_seconds)
        logger.info("Task memory set: %s:%s", task_id, key)

    async def get(self, task_id: str, key: str) -> str | None:
        r = await self._conn()
        val = await r.get(self._key(task_id, key))
        return val

    async def delete_all(self, task_id: str) -> None:
        r = await self._conn()
        pattern = f"task_memory:{task_id}:*"
        removed = 0
        async for key in r.scan_iter(match=pattern):
            await r.delete(key)
            removed += 1
        logger.info("Task memory deleted for task %s — %d key(s) removed", task_id, removed)

    async def exists(self, task_id: str, key: str) -> bool:
        r = await self._conn()
        return bool(await r.exists(self._key(task_id, key)))

    async def aclose(self) -> None:
        if self._redis is not None and self._owned_client:
            await self._redis.aclose()
            self._redis = None
