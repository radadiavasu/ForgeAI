"""Unit tests for ``FrontendSandbox`` (mocked Docker, no real containers)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from forgeai.sandbox.frontend_sandbox import FrontendSandbox
from forgeai.sandbox.sandbox import SandboxConfig
from forgeai.sandbox.schemas import RunnerOutput, SandboxTestCaseResult


def _cfg() -> SandboxConfig:
    return SandboxConfig(
        image="python:3.11-slim",
        cpu_limit=1.0,
        memory_limit="256m",
        timeout_low=120,
        timeout_medium=180,
        timeout_high=300,
        working_dir="/sandbox",
    )


def _playwright_json_report(*, passed: bool) -> bytes:
    status = "passed" if passed else "failed"
    err: dict = {}
    if not passed:
        err = {"message": "assertion failed"}
    report = {
        "suites": [
            {
                "title": "app",
                "specs": [
                    {
                        "title": "smoke",
                        "tests": [
                            {
                                "title": "loads",
                                "results": [{"status": status, "error": err if not passed else {}}],
                            }
                        ],
                    }
                ],
                "suites": [],
            }
        ],
        "stats": {"expected": 1, "unexpected": 0 if passed else 1, "duration": 42},
    }
    return json.dumps(report).encode("utf-8")


def _make_ctr_for_run(*, playwright_pass: bool) -> MagicMock:
    ctr = MagicMock()
    ctr.name = "forgeai-frontend-sandbox-mock"
    ctr.put_archive = MagicMock()
    ctr.start = MagicMock()
    ctr.stop = MagicMock()
    ctr.remove = MagicMock()
    ping = {"n": 0}

    def exec_run(cmd, workdir=None, detach=False, demux=False):
        s = cmd if isinstance(cmd, str) else " ".join(str(x) for x in cmd)
        if detach:
            return MagicMock(exit_code=0)
        if "playwright test" in s:
            body = _playwright_json_report(passed=playwright_pass)
            return MagicMock(exit_code=0 if playwright_pass else 1, output=(body, b""))
        if "node" in s and "5173" in s:
            ping["n"] += 1
            ok = ping["n"] >= 2
            return MagicMock(exit_code=0 if ok else 1, output=(b"", b""))
        return MagicMock(exit_code=0, output=(b"", b""))

    ctr.exec_run = MagicMock(side_effect=exec_run)
    return ctr


@pytest.mark.asyncio
async def test_frontend_sandbox_uses_configured_image() -> None:
    ctr = _make_ctr_for_run(playwright_pass=True)
    with patch("forgeai.sandbox.frontend_sandbox.docker.from_env") as df:
        client = MagicMock()
        client.containers.create = MagicMock(return_value=ctr)
        df.return_value = client
        fs = FrontendSandbox("LOW", _cfg())
        await fs.run("export default function App(){ return <div/> }", "import { test, expect } from '@playwright/test';\ntest('x', async () => {});\n")
        create_kw = client.containers.create.call_args
        assert create_kw[0][0] == fs.image
        assert "forgeai-frontend-sandbox" in fs.image


@pytest.mark.asyncio
async def test_container_destroyed_after_successful_run() -> None:
    ctr = _make_ctr_for_run(playwright_pass=True)
    with patch("forgeai.sandbox.frontend_sandbox.docker.from_env") as df:
        client = MagicMock()
        client.containers.create = MagicMock(return_value=ctr)
        df.return_value = client
        fs = FrontendSandbox("LOW", _cfg())
        await fs.run("export default function App(){ return <div id='root-app'/> }", "import { test, expect } from '@playwright/test';\ntest('x', async () => {});\n")
        assert ctr.remove.called


@pytest.mark.asyncio
async def test_container_destroyed_after_failed_run() -> None:
    ctr = _make_ctr_for_run(playwright_pass=False)
    with patch("forgeai.sandbox.frontend_sandbox.docker.from_env") as df:
        client = MagicMock()
        client.containers.create = MagicMock(return_value=ctr)
        df.return_value = client
        fs = FrontendSandbox("LOW", _cfg())
        out = await fs.run("export default function App(){ return <div/> }", "import { test, expect } from '@playwright/test';\ntest('x', async () => {});\n")
        assert out.success is False
        assert ctr.remove.called


@pytest.mark.asyncio
async def test_wait_for_server_returns_true_when_ready() -> None:
    ctr = MagicMock()
    ctr.exec_run = MagicMock(return_value=MagicMock(exit_code=0))
    fs = FrontendSandbox("LOW", _cfg())
    ok = await fs._wait_for_server(ctr, timeout=5)
    assert ok is True


@pytest.mark.asyncio
async def test_wait_for_server_returns_false_on_timeout() -> None:
    ctr = MagicMock()
    ctr.exec_run = MagicMock(return_value=MagicMock(exit_code=1))
    fs = FrontendSandbox("LOW", _cfg())
    ok = await fs._wait_for_server(ctr, timeout=1)
    assert ok is False


def test_parse_playwright_output_maps_passed_tests() -> None:
    fs = FrontendSandbox("LOW", _cfg())
    raw = _playwright_json_report(passed=True).decode()
    out = fs._parse_playwright_output(raw, execution_time=1.5, stderr="")
    assert out.success is True
    assert out.total_tests == 1
    assert out.passed_tests == 1
    assert out.failed_tests == 0
    assert out.test_cases[0].passed is True


def test_parse_playwright_output_maps_failed_tests() -> None:
    fs = FrontendSandbox("LOW", _cfg())
    raw = _playwright_json_report(passed=False).decode()
    out = fs._parse_playwright_output(raw, execution_time=2.0, stderr="err")
    assert out.success is False
    assert out.failed_tests == 1
    assert out.test_cases[0].passed is False
    assert out.test_cases[0].error


def test_runner_output_schema_compatible_pytest_and_playwright() -> None:
    """``RunnerOutput`` is shared by pytest ``TestRunner`` and Playwright sandbox."""
    py_out = RunnerOutput(
        success=True,
        total_tests=2,
        passed_tests=2,
        failed_tests=0,
        test_cases=[
            SandboxTestCaseResult(name="test_a", passed=True),
            SandboxTestCaseResult(name="test_b", passed=True),
        ],
        stdout="ok",
        stderr="",
        execution_time_seconds=0.1,
    )
    pw_out = RunnerOutput(
        success=False,
        total_tests=1,
        passed_tests=0,
        failed_tests=1,
        test_cases=[SandboxTestCaseResult(name="suite :: spec :: loads", passed=False, error="e")],
        stdout="{}",
        stderr="",
        execution_time_seconds=0.2,
        sandbox_error="",
    )
    assert py_out.model_dump().keys() == pw_out.model_dump().keys()
