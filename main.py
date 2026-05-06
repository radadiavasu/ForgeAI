"""Phase 2 proof-of-life: run sandbox-backed QA cycle end-to-end."""

import asyncio
import logging
import sys
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.config import get_settings
from forgeai.database import AsyncSessionFactory
from forgeai.models.task import TaskComplexity
from forgeai.sandbox.runner import TestRunner
from forgeai.sandbox.sandbox import Sandbox, SandboxConfig
from forgeai.state_machine.machine import TaskStateMachine

_DEMO_CODE = """def generate_token(user_id: str) -> str:
    import hashlib
    return hashlib.sha256(f"token:{user_id}".encode()).hexdigest()

def validate_token(token: str, user_id: str) -> bool:
    expected = generate_token(user_id)
    return token == expected
"""

_DEMO_TEST_CODE = """from main import generate_token, validate_token

def test_generate_token_returns_string():
    token = generate_token("user_123")
    assert isinstance(token, str)
    assert len(token) == 64

def test_validate_token_correct():
    token = generate_token("user_123")
    assert validate_token(token, "user_123") is True

def test_validate_token_wrong_user():
    token = generate_token("user_123")
    assert validate_token(token, "user_456") is False

def test_validate_token_empty():
    token = generate_token("")
    assert validate_token(token, "") is True
"""


def _configure_logging() -> None:
    """Set root log level so library INFO/WARNING lines appear when desired."""
    logging.basicConfig(level=logging.INFO)


def _connection_refused(exc: BaseException) -> bool:
    """Return True if ``exc`` (or its cause chain) is a refused TCP connection."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ConnectionRefusedError):
            return True
        if isinstance(cur, OSError) and getattr(cur, "winerror", None) == 1225:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _print_database_help() -> None:
    """Print how to start Postgres when nothing is listening on the DB port."""
    print(
        "[FORGEAI] Cannot reach PostgreSQL (connection refused).\n"
        "\n"
        "1. Start Docker Desktop (Windows), then from the project root run:\n"
        "     docker compose up -d\n"
        "2. Apply schema:\n"
        "     python -m alembic upgrade head\n"
        "3. Copy .env.example to .env if you use a non-default DATABASE_URL.\n"
        "\n"
        "If you use a local Postgres install instead of Docker, set DATABASE_URL "
        "in .env and ensure the server is running.",
        file=sys.stderr,
    )


async def _run_cycle(session: AsyncSession) -> UUID:
    """Execute the scripted lifecycle and print milestone lines.

    Args:
        session: Shared async DB session for all agents.

    Returns:
        The task id used for the demo run.
    """
    settings = get_settings()
    sandbox = Sandbox(
        complexity=TaskComplexity.MEDIUM.value,
        config=SandboxConfig(
            image=settings.sandbox_image,
            cpu_limit=settings.sandbox_cpu_limit,
            memory_limit=settings.sandbox_memory_limit,
            timeout_low=settings.sandbox_timeout_low,
            timeout_medium=settings.sandbox_timeout_medium,
            timeout_high=settings.sandbox_timeout_high,
            working_dir=settings.sandbox_working_dir,
        ),
    )
    runner = TestRunner(sandbox)
    lead = LeadAgent("lead_agent_1", session)
    backend = BackendAgent("backend_agent_1", session)
    qa = QAAgent("qa_agent_1", session, test_runner=runner)

    task = await lead.create_task(
        title="Build Auth API",
        description=None,
        complexity=TaskComplexity.MEDIUM,
        assigned_agent="backend_agent_1",
    )
    print(
        f"[FORGEAI] Task created: {task.title} | State: {task.current_state.value}"
    )

    task = await lead.approve_phase_transition(task.id)
    print(f"[FORGEAI] Phase transition approved | State: {task.current_state.value}")

    task = await lead.assign_task(task.id)
    print(
        f"[FORGEAI] Task assigned to {task.assigned_agent} | State: "
        f"{task.current_state.value}"
    )

    task = await backend.complete_work(task.id, output="JWT auth implemented")
    print(
        f"[FORGEAI] Work completed by {task.assigned_agent} | State: "
        f"{task.current_state.value}"
    )

    task = await qa.begin_review(task.id)
    print(f"[FORGEAI] QA review started | State: {task.current_state.value}")

    print("[FORGEAI] Sandbox executing tests...")
    output = await qa.review(task.id, code=_DEMO_CODE, test_code=_DEMO_TEST_CODE)
    print(
        f"[FORGEAI] Tests complete: {output.passed_tests}/{output.total_tests} passed "
        f"in {output.execution_time_seconds:.2f}s"
    )
    if output.success:
        task = await qa.approve(task.id, output="JWT auth implemented")
        print(f"[FORGEAI] QA approved | State: {task.current_state.value}")
    else:
        defect_report = output.sandbox_error or "Test failures detected in sandbox run"
        task = await qa.reject(task.id, defect_report=defect_report)
        print(f"[FORGEAI] QA rejected | State: {task.current_state.value}")

    print()
    print("--- RUNNER OUTPUT ---")
    print(f"Success: {output.success}")
    print(
        f"Total: {output.total_tests} | Passed: {output.passed_tests} | "
        f"Failed: {output.failed_tests}"
    )
    for case in output.test_cases:
        mark = "PASS" if case.passed else "FAIL"
        print(f"  {mark} {case.name}")

    machine = TaskStateMachine(session)
    history = await machine.get_history(task.id)

    print()
    print("--- FULL STATE HISTORY ---")
    arrow_width = 29
    agent_width = 15
    for i, row in enumerate(history, start=1):
        arrow = f"{row.from_state.value} -> {row.to_state.value}"
        agent_cell = row.agent_id.ljust(agent_width)
        print(
            f"{i}. {arrow.ljust(arrow_width)}| agent: {agent_cell}| success: {row.success}"
        )

    return task.id


async def async_main() -> None:
    """Entrypoint: open one session and run the scripted demo."""
    try:
        async with AsyncSessionFactory() as session:
            await _run_cycle(session)
    except Exception as e:
        if _connection_refused(e):
            _print_database_help()
            raise SystemExit(1) from e
        raise


def main() -> None:
    """Console script entrypoint."""
    _configure_logging()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
