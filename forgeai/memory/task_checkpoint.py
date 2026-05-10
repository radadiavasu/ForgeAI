"""MinIO-backed checkpoints for in-progress task snapshots."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from typing import Any

from minio import Minio
from minio.error import S3Error

from forgeai.exceptions import CheckpointNotFoundError

logger = logging.getLogger(__name__)


class TaskCheckpoint:
    """Save and restore JSON checkpoints on S3-compatible MinIO storage."""

    def __init__(
        self,
        minio_endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        self._endpoint = minio_endpoint
        self._bucket = bucket
        self._secure = secure
        self._client = Minio(
            minio_endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def _ensure_bucket_sync(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    async def _ensure_bucket_async(self) -> None:
        await asyncio.to_thread(self._ensure_bucket_sync)

    async def save(self, task_id: str, agent_id: str, checkpoint_data: dict[str, Any]) -> str:
        await self._ensure_bucket_async()
        ts = int(time.time() * 1000)
        object_path = f"checkpoints/{task_id}/{agent_id}/{ts}.json"
        payload = json.dumps(checkpoint_data, separators=(",", ":")).encode("utf-8")

        def _put() -> None:
            self._client.put_object(
                self._bucket,
                object_path,
                io.BytesIO(payload),
                length=len(payload),
                content_type="application/json",
            )

        await asyncio.to_thread(_put)
        logger.info("Checkpoint saved: %s", object_path)
        return object_path

    async def load(self, object_path: str) -> dict[str, Any]:
        def _get() -> bytes:
            try:
                resp = self._client.get_object(self._bucket, object_path)
                try:
                    return resp.read()
                finally:
                    resp.close()
                    resp.release_conn()
            except S3Error as exc:
                if exc.code == "NoSuchKey":
                    raise CheckpointNotFoundError(f"No checkpoint at {object_path}") from exc
                raise

        data = await asyncio.to_thread(_get)
        return json.loads(data.decode("utf-8"))

    async def delete(self, task_id: str) -> None:
        prefix = f"checkpoints/{task_id}/"

        def _list_and_remove() -> None:
            to_remove = [
                obj.object_name
                for obj in self._client.list_objects(self._bucket, prefix=prefix, recursive=True)
            ]
            for name in to_remove:
                self._client.remove_object(self._bucket, name)

        await asyncio.to_thread(_list_and_remove)
        logger.info("Checkpoints deleted for task %s", task_id)

    async def get_latest(self, task_id: str, agent_id: str) -> dict[str, Any] | None:
        prefix = f"checkpoints/{task_id}/{agent_id}/"

        def _list_names() -> list[str]:
            return [
                obj.object_name
                for obj in self._client.list_objects(self._bucket, prefix=prefix, recursive=True)
            ]

        names = await asyncio.to_thread(_list_names)
        if not names:
            return None

        def _ts_from_path(p: str) -> int:
            base = p.rsplit("/", 1)[-1]
            stem = base.removesuffix(".json")
            try:
                return int(stem)
            except ValueError:
                return 0

        latest_path = max(names, key=_ts_from_path)
        return await self.load(latest_path)
