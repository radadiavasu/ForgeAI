"""Tests for TestRunner parsing and execution integration contract."""

import pytest

from forgeai.sandbox.runner import TestRunner
from forgeai.sandbox.schemas import RunnerOutput


class _FakeSandbox:
    def __init__(self, output: RunnerOutput) -> None:
        self._output = output

    async def run(self, code: str, test_code: str) -> RunnerOutput:
        return self._output


def _make_output(stdout: str, stderr: str = "") -> RunnerOutput:
    return RunnerOutput(
        success=False,
        total_tests=0,
        passed_tests=0,
        failed_tests=0,
        test_cases=[],
        stdout=stdout,
        stderr=stderr,
        execution_time_seconds=1.23,
    )


@pytest.mark.asyncio
async def test_passing_tests_return_success_true() -> None:
    stdout = """
test_main.py::test_one PASSED
test_main.py::test_two PASSED
======================== 2 passed in 0.20s ========================
"""
    runner = TestRunner(_FakeSandbox(_make_output(stdout)))
    result = await runner.run("code", "tests")
    assert result.success is True
    assert result.total_tests == 2
    assert result.passed_tests == 2
    assert result.failed_tests == 0


@pytest.mark.asyncio
async def test_failing_tests_return_success_false() -> None:
    stdout = """
test_main.py::test_one FAILED
======================== 0 passed, 1 failed in 0.30s ========================
"""
    runner = TestRunner(_FakeSandbox(_make_output(stdout)))
    result = await runner.run("code", "tests")
    assert result.success is False
    assert result.total_tests == 1
    assert result.failed_tests == 1


@pytest.mark.asyncio
async def test_partial_failures_are_counted() -> None:
    stdout = """
test_main.py::test_one PASSED
test_main.py::test_two FAILED
test_main.py::test_three PASSED
======================== 2 passed, 1 failed in 0.40s ========================
"""
    runner = TestRunner(_FakeSandbox(_make_output(stdout)))
    result = await runner.run("code", "tests")
    assert result.total_tests == 3
    assert result.passed_tests == 2
    assert result.failed_tests == 1
    assert result.success is False


@pytest.mark.asyncio
async def test_test_cases_contains_one_entry_per_test() -> None:
    stdout = """
test_main.py::test_alpha PASSED
test_main.py::test_beta FAILED
======================== 1 passed, 1 failed in 0.11s ========================
"""
    runner = TestRunner(_FakeSandbox(_make_output(stdout)))
    result = await runner.run("code", "tests")
    assert len(result.test_cases) == 2
    assert {case.name for case in result.test_cases} == {"test_alpha", "test_beta"}


@pytest.mark.asyncio
async def test_syntax_error_returns_failed_output_with_stderr() -> None:
    output = _make_output(stdout="collecting ...", stderr="SyntaxError: invalid syntax")
    runner = TestRunner(_FakeSandbox(output))
    result = await runner.run("broken", "tests")
    assert result.success is False
    assert "SyntaxError" in result.stderr


def test_parse_pytest_output_known_string() -> None:
    stdout = """
test_main.py::test_a PASSED
test_main.py::test_b FAILED
======================== 1 passed, 1 failed in 0.57s ========================
"""
    runner = TestRunner(_FakeSandbox(_make_output(stdout)))
    result = runner._parse_pytest_output(stdout, "", 9.0)
    assert result.total_tests == 2
    assert result.passed_tests == 1
    assert result.failed_tests == 1
    assert result.execution_time_seconds == pytest.approx(0.57)
