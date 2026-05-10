"""Tests for TaskCheckpoint (MinIO client, mocked)."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest

from forgeai.exceptions import CheckpointNotFoundError
from forgeai.memory.task_checkpoint import TaskCheckpoint


@pytest.fixture
def checkpoint(monkeypatch: pytest.MonkeyPatch) -> TaskCheckpoint:
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = False

    def _make_bucket(name: str) -> None:
        mock_client.bucket_exists.return_value = True

    mock_client.make_bucket.side_effect = _make_bucket

    monkeypatch.setattr(
        "forgeai.memory.task_checkpoint.Minio",
        lambda *a, **k: mock_client,
    )
    tc = TaskCheckpoint("localhost:9000", "k", "s", "bkt", secure=False)
    tc._client = mock_client  # type: ignore[attr-defined]
    return tc


@pytest.mark.asyncio
async def test_save_returns_object_path(checkpoint: TaskCheckpoint) -> None:
    path = await checkpoint.save(
        "task-uuid-1111",
        "backend_agent_1",
        {"progress": "50%", "last_step": "schema defined"},
    )
    assert path.startswith("checkpoints/task-uuid-1111/backend_agent_1/")
    assert path.endswith(".json")


@pytest.mark.asyncio
async def test_load_retrieves_data(checkpoint: TaskCheckpoint) -> None:
    payload = b'{"a": 1}'
    resp = MagicMock()
    resp.read.return_value = payload
    resp.close = MagicMock()
    resp.release_conn = MagicMock()
    checkpoint._client.get_object.return_value = resp  # type: ignore[attr-defined]

    data = await checkpoint.load("checkpoints/t/a/1.json")
    assert data == {"a": 1}


@pytest.mark.asyncio
async def test_load_missing_raises(checkpoint: TaskCheckpoint) -> None:
    from minio.error import S3Error

    def _raise(*_a, **_k):
        raise S3Error(None, "NoSuchKey", "not found", None, None, None)

    checkpoint._client.get_object.side_effect = _raise  # type: ignore[attr-defined]

    with pytest.raises(CheckpointNotFoundError):
        await checkpoint.load("checkpoints/missing/path.json")


@pytest.mark.asyncio
async def test_get_latest_most_recent(checkpoint: TaskCheckpoint) -> None:
    objs = [
        MagicMock(object_name="checkpoints/t/ag/100.json"),
        MagicMock(object_name="checkpoints/t/ag/300.json"),
        MagicMock(object_name="checkpoints/t/ag/200.json"),
    ]
    checkpoint._client.list_objects.return_value = objs  # type: ignore[attr-defined]
    payload = b'{"latest": true}'
    resp = MagicMock()
    resp.read.return_value = payload
    resp.close = MagicMock()
    resp.release_conn = MagicMock()
    checkpoint._client.get_object.return_value = resp  # type: ignore[attr-defined]

    data = await checkpoint.get_latest("t", "ag")
    assert data == {"latest": True}
    checkpoint._client.get_object.assert_called()


@pytest.mark.asyncio
async def test_get_latest_none_when_empty(checkpoint: TaskCheckpoint) -> None:
    checkpoint._client.list_objects.return_value = []  # type: ignore[attr-defined]
    assert await checkpoint.get_latest("t", "ag") is None


@pytest.mark.asyncio
async def test_delete_removes_task_prefix(checkpoint: TaskCheckpoint) -> None:
    checkpoint._client.list_objects.return_value = [  # type: ignore[attr-defined]
        MagicMock(object_name="checkpoints/t1/ag/1.json"),
        MagicMock(object_name="checkpoints/t1/ag/2.json"),
    ]
    await checkpoint.delete("t1")
    assert checkpoint._client.remove_object.call_count == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_delete_no_objects_no_raise(checkpoint: TaskCheckpoint) -> None:
    checkpoint._client.list_objects.return_value = []  # type: ignore[attr-defined]
    await checkpoint.delete("nothing")
