"""Phase 3 demo: happy path, escalation ladder path, and drift detection."""

import asyncio
import logging
import sys

from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.config import get_settings
from forgeai.database import AsyncSessionFactory
from forgeai.escalation import EscalationLadder, LoopCounter
from forgeai.exceptions import AlreadyEscalatedError
from forgeai.models.task import TaskComplexity
from forgeai.monitoring import DriftMonitor
from forgeai.sandbox.runner import TestRunner
from forgeai.sandbox.sandbox import Sandbox, SandboxConfig

_HAPPY_CODE = """def generate_token(user_id: str) -> str:
    import hashlib
    return hashlib.sha256(f"token:{user_id}".encode()).hexdigest()

def validate_token(token: str, user_id: str) -> bool:
    expected = generate_token(user_id)
    return token == expected
"""

_HAPPY_TEST_CODE = """from main import generate_token, validate_token

def test_generate_token_returns_string():
    token = generate_token("user_123")
    assert isinstance(token, str)
    assert len(token) == 64

def test_validate_token_correct():
    token = generate_token("user_123")
    assert validate_token(token, "user_123") is True
"""

_BROKEN_PAYMENT_CODE = """def process_payment(amount: float) -> dict:
    # Intentionally wrong — returns nothing useful
    return {}
"""

_BROKEN_PAYMENT_TESTS = """from main import process_payment

def test_payment_returns_transaction_id():
    result = process_payment(99.99)
    assert "transaction_id" in result

def test_payment_returns_status():
    result = process_payment(99.99)
    assert result.get("status") == "success"
"""


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO)


def _connection_refused(exc: BaseException) -> bool:
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
    print(
        "[FORGEAI] Cannot reach PostgreSQL (connection refused).\n"
        "Start Docker and run `docker compose up -d`, then apply migrations via "
        "`python -m alembic upgrade head`.",
        file=sys.stderr,
    )


def _make_runner(complexity: TaskComplexity) -> TestRunner:
    settings = get_settings()
    sandbox = Sandbox(
        complexity=complexity.value,
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
    return TestRunner(sandbox)


async def _run_happy_path(session: AsyncSession) -> None:
    print("=== RUN 1: HAPPY PATH ===")
    lead = LeadAgent("lead_agent_1", session)
    backend = BackendAgent("backend_agent_1", session)
    qa = QAAgent("qa_agent_1", session, test_runner=_make_runner(TaskComplexity.MEDIUM))

    task = await lead.create_task("Build Auth API", None, TaskComplexity.MEDIUM, "backend_agent_1")
    print(f"[FORGEAI] Task created: {task.title} | State: {task.current_state.value}")
    task = await lead.approve_phase_transition(task.id)
    print(f"[FORGEAI] Phase transition approved | State: {task.current_state.value}")
    task = await lead.assign_task(task.id)
    print(f"[FORGEAI] Task assigned to {task.assigned_agent} | State: {task.current_state.value}")
    task = await backend.complete_work(task.id, output="JWT auth implemented")
    print(f"[FORGEAI] Work completed by {task.assigned_agent} | State: {task.current_state.value}")
    task = await qa.begin_review(task.id)
    print(f"[FORGEAI] QA review started | State: {task.current_state.value}")
    output = await qa.review(task.id, code=_HAPPY_CODE, test_code=_HAPPY_TEST_CODE)
    if output.success:
        task = await qa.approve(task.id, output="JWT auth implemented")
        print(f"[FORGEAI] QA approved | State: {task.current_state.value}")
    else:
        task = await qa.reject(task.id, defect_report=output.sandbox_error or "Test failures")
        print(f"[FORGEAI] QA rejected | State: {task.current_state.value}")
    print()


async def _run_escalation_path(session: AsyncSession) -> None:
    print("=== RUN 2: ESCALATION PATH ===")
    settings = get_settings()
    lead = LeadAgent("lead_agent_1", session)
    backend = BackendAgent("backend_agent_1", session)
    qa = QAAgent("qa_agent_1", session, test_runner=_make_runner(TaskComplexity.HIGH))
    loop_counter = LoopCounter()
    ladder = EscalationLadder(loop_counter=loop_counter, max_self_retries=settings.max_self_retries)

    task = await lead.create_task("Build Payment API", None, TaskComplexity.HIGH, "backend_agent_1")
    print(f"[FORGEAI] Task created: {task.title} | State: {task.current_state.value}")
    task = await lead.approve_phase_transition(task.id)
    print(f"[FORGEAI] Phase transition approved | State: {task.current_state.value}")
    task = await lead.assign_task(task.id)
    print(f"[FORGEAI] Task assigned to {task.assigned_agent} | State: {task.current_state.value}")
    task = await backend.complete_work(task.id, output="Payment API implemented")
    task = await qa.begin_review(task.id)
    output = await qa.review(task.id, code=_BROKEN_PAYMENT_CODE, test_code=_BROKEN_PAYMENT_TESTS)
    if output.success:
        await qa.approve(task.id, output="Payment API implemented")
    else:
        print("[FORGEAI] Tests failed — initiating escalation")

    task_id = str(task.id)
    for attempt in range(1, 4):
        try:
            result = await ladder.escalate(
                task_id=task_id,
                agent_id=lead.agent_id,
                error_signature="test_failure:assertion_error",
                error_detail="test failures that could not be resolved automatically",
                task_specification="Build Payment API",
            )
            if attempt == 3:
                print("--- Loop_Counter threshold demonstration ---")
            if attempt == 3 and loop_counter.should_escalate(task_id, "test_failure:assertion_error"):
                print("[ESCALATION] Same error seen 3 times — skipping Level 1, jumping to Level 2")
            if result.needs_human_input:
                print("[ESCALATION] Level 5: Human input required")
                print(f"[FORGEAI] [WARNING] Task needs human input: {result.human_message}")
            break
        except AlreadyEscalatedError:
            if attempt < 3:
                continue
            print("[FORGEAI] Task already at Level 5 and blocked until human input.")
    print()


def process_payment(amount: float) -> dict:
    """Demo function intentionally used by escalation-path test code."""
    _ = amount
    return {}


def _print_drift_result(label: str, result) -> None:
    print(f"{label}:")
    print(f"  Drift score: {result.score}/100")
    print(f"  Is drifting: {result.is_drifting}")
    print(f"  Description: {result.description}")


def _run_drift_detection() -> None:
    print("=== RUN 3: DRIFT DETECTION ===")
    settings = get_settings()
    monitor = DriftMonitor(threshold=settings.drift_threshold)
    task_specification = (
        "Build a JWT authentication API that validates tokens and returns user roles"
    )
    on_track_output = (
        "Implemented JWT token validation with role-based access control. "
        "Tokens are verified using HS256 algorithm."
    )
    drifted_output = "Built a shopping cart with product listing and price calculation features."

    print("Task specification: Build a JWT authentication API...")
    print()
    _print_drift_result("Output 1 (on-track)", monitor.check(task_specification, on_track_output))
    print()
    _print_drift_result("Output 2 (drifted)", monitor.check(task_specification, drifted_output))


async def async_main() -> None:
    try:
        async with AsyncSessionFactory() as session:
            await _run_happy_path(session)
            await _run_escalation_path(session)
        _run_drift_detection()
    except Exception as e:
        if _connection_refused(e):
            _print_database_help()
            raise SystemExit(1) from e
        raise


def main() -> None:
    _configure_logging()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
