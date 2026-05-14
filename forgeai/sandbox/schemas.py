"""Schemas used by sandbox execution and test runner parsing."""

from pydantic import BaseModel


class SandboxTestCaseResult(BaseModel):
    """Result of an individual test case."""

    name: str
    passed: bool
    stdout: str = ""
    error: str = ""


class RunnerOutput(BaseModel):
    """Structured output from one sandbox test run."""

    success: bool
    total_tests: int
    passed_tests: int
    failed_tests: int
    test_cases: list[SandboxTestCaseResult]
    stdout: str
    stderr: str
    execution_time_seconds: float
    timed_out: bool = False
    sandbox_error: str = ""
