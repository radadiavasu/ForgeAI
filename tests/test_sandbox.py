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
