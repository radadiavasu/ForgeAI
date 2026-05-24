"""Tests for Docker sandbox provisioning, isolation, and cleanup."""

from __future__ import annotations

import docker
import pytest

from forgeai.exceptions import SandboxTimeoutError
from forgeai.sandbox.sandbox import Sandbox, SandboxConfig


def _docker_available() -> bool:
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(), reason="Docker daemon unavailable for sandbox tests"
)


def _config(timeout_low: int = 15) -> SandboxConfig:
    return SandboxConfig(
        image="python:3.11-slim",
        cpu_limit=1.0,
        memory_limit="256m",
        timeout_low=timeout_low,
        timeout_medium=30,
        timeout_high=60,
        working_dir="/sandbox",
    )


@pytest.mark.asyncio
async def test_container_provisioned_and_destroyed_in_one_run() -> None:
    sandbox = Sandbox("LOW", _config())
    out = await sandbox.run("def f():\n    return 1\n", "from main import f\n\ndef test_f():\n    assert f() == 1\n")
    assert "PASSED" in out.stdout
    containers = docker.from_env().containers.list(all=True, filters={"name": "forgeai-sandbox-"})
    assert containers == []


@pytest.mark.asyncio
async def test_network_mode_none_is_enforced() -> None:
    sandbox = Sandbox("LOW", _config())
    code = "def noop():\n    return True\n"
    test_code = """
import socket

def test_no_network_egress():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        err = s.connect_ex(("1.1.1.1", 53))
        assert err != 0
"""
    out = await sandbox.run(code, test_code)
    assert "PASSED" in out.stdout


@pytest.mark.asyncio
async def test_timeout_raises_sandbox_timeout_error() -> None:
    sandbox = Sandbox("LOW", _config(timeout_low=1))
    code = "def noop():\n    return True\n"
    test_code = """
import time

def test_sleep():
    time.sleep(3)
    assert True
"""
    with pytest.raises(SandboxTimeoutError):
        await sandbox.run(code, test_code)


@pytest.mark.asyncio
async def test_container_destroyed_when_execution_fails() -> None:
    sandbox = Sandbox("LOW", _config())
    code = "def broken(:\n    return 1\n"
    test_code = "def test_dummy():\n    assert True\n"
    out = await sandbox.run(code, test_code)
    assert out.stderr or out.stdout
    containers = docker.from_env().containers.list(all=True, filters={"name": "forgeai-sandbox-"})
    assert containers == []


@pytest.mark.asyncio
async def test_written_code_executes_correctly() -> None:
    sandbox = Sandbox("LOW", _config())
    code = "def add(a, b):\n    return a + b\n"
    test_code = "from main import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    out = await sandbox.run(code, test_code)
    assert "1 passed" in out.stdout


@pytest.mark.asyncio
async def test_vitest_sandbox_uses_node_image() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from forgeai.sandbox.schemas import RunnerOutput

    sandbox = Sandbox("LOW", _config())
    mock_container = MagicMock()
    mock_container.name = "forgeai-vitest-test"
    exec_result = MagicMock()
    exec_result.exit_code = 0
    exec_result.output = (b" PASS module.test.js\n", b"")

    mock_client = MagicMock()
    mock_client.containers.create.return_value = mock_container
    sandbox._client = mock_client
    sandbox._ensure_vitest_image = MagicMock(return_value="node:18-alpine")
    sandbox._write_file = AsyncMock()
    sandbox._destroy = AsyncMock()
    mock_container.start = MagicMock()
    mock_container.exec_run = MagicMock(return_value=exec_result)

    async def sync_to_thread(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch("forgeai.sandbox.sandbox.asyncio.to_thread", side_effect=sync_to_thread):
        out = await sandbox.run_vitest(
            "export const x = 1;",
            "import { expect, test } from 'vitest';\ntest('ok', () => expect(1).toBe(1));",
        )

    assert isinstance(out, RunnerOutput)
    assert out.success is True
    mock_client.containers.create.assert_called_once()
    create_args = mock_client.containers.create.call_args
    assert create_args.args[0] == "node:18-alpine"
    mock_container.start.assert_called_once()
    mock_container.exec_run.assert_called_once()


@pytest.mark.asyncio
async def test_vitest_routes_on_vitest_framework(db_session) -> None:
    from unittest.mock import AsyncMock, MagicMock, patch
    import uuid

    from forgeai.agents.qa_agent import QAAgent
    from forgeai.llm.schemas import TechStackDocument
    from forgeai.sandbox.runner import TestRunner
    from forgeai.sandbox.schemas import RunnerOutput

    mock_sandbox = MagicMock()
    mock_sandbox.run = AsyncMock(
        return_value=RunnerOutput(
            success=True,
            total_tests=1,
            passed_tests=1,
            failed_tests=0,
            test_cases=[],
            stdout="PASSED",
            stderr="",
            execution_time_seconds=0.1,
        )
    )
    mock_sandbox.run_vitest = AsyncMock(
        return_value=RunnerOutput(
            success=True,
            total_tests=1,
            passed_tests=1,
            failed_tests=0,
            test_cases=[],
            stdout=" test passed",
            stderr="",
            execution_time_seconds=0.1,
        )
    )
    tr = TestRunner(mock_sandbox)
    qa = QAAgent("qa_agent_1", db_session, test_runner=tr)

    tech = TechStackDocument(
        language="JavaScript",
        framework="Express.js",
        database="PostgreSQL",
        testing_framework="Vitest",
        rationale="test",
    )
    tid = uuid.uuid4()
    pid = uuid.uuid4()

    with patch(
        "forgeai.agents.qa_agent.load_tech_stack_document",
        new=AsyncMock(return_value=tech),
    ), patch.object(
        qa,
        "_assert_not_self_approval",
        new=AsyncMock(),
    ), patch.object(
        qa.db,
        "execute",
        new=AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(
                    return_value=MagicMock(project_id=pid)
                )
            )
        ),
    ):
        await qa.review(tid, "export const x = 1;", "test('x', () => {});")

    mock_sandbox.run_vitest.assert_awaited_once()
    mock_sandbox.run.assert_not_called()
