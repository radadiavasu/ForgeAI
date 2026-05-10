"""Loop counter backed by Redis — repeated error signatures per task."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import redis.asyncio as redis

from forgeai.config import get_settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class LoopCounter:
    """Track repeated failures in Redis; escalation triggers after threshold."""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        redis_client: redis.Redis | None = None,
    ) -> None:
        self._redis_url = redis_url or get_settings().redis_url
        self._redis: redis.Redis | None = redis_client
        self._owned_client = redis_client is None

    def _hash_key(self, task_id: str) -> str:
        return f"loop_counter:{task_id}"

    async def _conn(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def increment(self, task_id: str, error_signature: str) -> int:
        r = await self._conn()
        n = await r.hincrby(self._hash_key(task_id), error_signature, 1)
        return int(n)

    async def get(self, task_id: str, error_signature: str) -> int:
        r = await self._conn()
        v = await r.hget(self._hash_key(task_id), error_signature)
        return int(v) if v is not None else 0

    async def reset(self, task_id: str) -> None:
        r = await self._conn()
        await r.delete(self._hash_key(task_id))

    async def should_escalate(self, task_id: str, error_signature: str) -> bool:
        return await self.get(task_id, error_signature) >= 3

    async def aclose(self) -> None:
        if self._redis is not None and self._owned_client:
            await self._redis.aclose()
            self._redis = None
