"""Test runner that submits code to sandbox and parses pytest output."""

from __future__ import annotations

import re
from typing import Final

from forgeai.exceptions import SandboxProvisionError, SandboxTimeoutError
from forgeai.sandbox.sandbox import Sandbox
from forgeai.sandbox.schemas import RunnerOutput, SandboxTestCaseResult

_RESULT_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\S+::\S+)\s+(PASSED|FAILED)(?:\s+\[[^\]]+\])?\s*$",
    re.MULTILINE,
)
_SUMMARY_RE: Final[re.Pattern[str]] = re.compile(
    r"=+\s+(.+?)\s+in\s+([0-9]*\.?[0-9]+)s\s+=+"
)


class TestRunner:
    """Submit code/tests to sandbox and return structured result."""

    __test__ = False

    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox

    async def run(self, code: str, test_code: str) -> RunnerOutput:
        """Execute tests in sandbox and parse output."""
        try:
            output = await self.sandbox.run(code=code, test_code=test_code)
        except SandboxTimeoutError as exc:
            return RunnerOutput(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                test_cases=[],
                stdout="",
                stderr="",
                execution_time_seconds=0.0,
                timed_out=True,
                sandbox_error=str(exc),
            )
        except SandboxProvisionError as exc:
            return RunnerOutput(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                test_cases=[],
                stdout="",
                stderr="",
                execution_time_seconds=0.0,
                timed_out=False,
                sandbox_error=str(exc),
            )
        return self._parse_pytest_output(
            stdout=output.stdout,
            stderr=output.stderr,
            execution_time=output.execution_time_seconds,
            timed_out=output.timed_out,
            sandbox_error=output.sandbox_error,
        )

    def _parse_pytest_output(
        self,
        stdout: str,
        stderr: str,
        execution_time: float,
        timed_out: bool = False,
        sandbox_error: str = "",
    ) -> RunnerOutput:
        """Parse pytest -v output into a fully populated runner output."""
        if timed_out:
            return RunnerOutput(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                test_cases=[],
                stdout=stdout,
                stderr=stderr,
                execution_time_seconds=execution_time,
                timed_out=True,
                sandbox_error=sandbox_error or "Sandbox execution timed out",
            )

        if not stdout.strip():
            return RunnerOutput(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                test_cases=[],
                stdout=stdout,
                stderr=stderr,
                execution_time_seconds=execution_time,
                sandbox_error=sandbox_error or "No output captured",
            )

        test_cases: list[SandboxTestCaseResult] = []
        for name, status in _RESULT_LINE_RE.findall(stdout):
            test_name = name.split("::", maxsplit=1)[-1].strip()
            passed = status == "PASSED"
            test_cases.append(
                SandboxTestCaseResult(name=test_name, passed=passed, stdout="", error="")
            )

        if not test_cases:
            return RunnerOutput(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                test_cases=[],
                stdout=stdout,
                stderr=stderr,
                execution_time_seconds=execution_time,
                sandbox_error=sandbox_error or "No output captured",
            )

        passed_tests = sum(1 for case in test_cases if case.passed)
        total_tests = len(test_cases)
        failed_tests = total_tests - passed_tests

        summary = _SUMMARY_RE.search(stdout)
        parsed_time = execution_time
        if summary is not None:
            parsed_time = float(summary.group(2))

        return RunnerOutput(
            success=failed_tests == 0,
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            test_cases=test_cases,
            stdout=stdout,
            stderr=stderr,
            execution_time_seconds=parsed_time,
            timed_out=False,
            sandbox_error=sandbox_error,
        )
