"""Phase 4 demo: Redis task memory, Chroma lessons, MinIO checkpoints, escalation DB."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.config import get_settings
from forgeai.database import AsyncSessionFactory
from forgeai.escalation import EscalationLadder, EscalationPersistence, LoopCounter
from forgeai.exceptions import AlreadyEscalatedError
from forgeai.memory.agent_memory import AgentMemory, new_lesson_id
from forgeai.memory.schemas import Lesson
from forgeai.memory.task_checkpoint import TaskCheckpoint
from forgeai.memory.task_memory import TaskMemory
from forgeai.models.escalation import EscalationEventModel
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


async def _run_task_memory_path(session: AsyncSession) -> None:
    print("=== RUN 1: TASK MEMORY ===")
    settings = get_settings()
    tm = TaskMemory(settings.redis_url, ttl_seconds=settings.task_memory_ttl)
    lead = LeadAgent("lead_agent_1", session, task_memory=tm)
    backend = BackendAgent("backend_agent_1", session, task_memory=tm)
    qa = QAAgent(
        "qa_agent_1",
        session,
        test_runner=_make_runner(TaskComplexity.MEDIUM),
        task_memory=tm,
    )

    task = await lead.create_task("Build Auth API", None, TaskComplexity.MEDIUM, "backend_agent_1")
    print(f"[FORGEAI] Task created: {task.title} | State: {task.current_state.value}")
    task = await lead.approve_phase_transition(task.id)
    print(f"[FORGEAI] Phase transition approved | State: {task.current_state.value}")
    task = await lead.assign_task(task.id)
    print(f"[FORGEAI] Task assigned to {task.assigned_agent} | State: {task.current_state.value}")
    tid = str(task.id)
    await tm.set(tid, "approach", "JWT with HS256")
    print("[MEMORY] Task memory set: approach = JWT with HS256")
    task = await backend.complete_work(task.id, output="JWT auth implemented")
    print(f"[FORGEAI] Work completed by {task.assigned_agent} | State: {task.current_state.value}")
    task = await qa.begin_review(task.id)
    v = await tm.get(tid, "approach")
    print(f"[MEMORY] Task memory verified during TESTING: {v}")
    output = await qa.review(task.id, code=_HAPPY_CODE, test_code=_HAPPY_TEST_CODE)
    if output.success:
        task = await qa.approve(task.id, output="JWT auth implemented")
        print(f"[FORGEAI] QA approved | State: {task.current_state.value}")
    else:
        task = await qa.reject(task.id, defect_report=output.sandbox_error or "Test failures")
        print(f"[FORGEAI] QA rejected | State: {task.current_state.value}")
    gone = await tm.get(tid, "approach")
    if gone is None:
        print("[MEMORY] Task memory deleted on DONE — 1 key removed")
    else:
        print("[MEMORY] Task memory still present after DONE (unexpected)")
    print()


async def _run_lesson_demo() -> None:
    print("=== RUN 2: LESSON WRITE AND RETRIEVAL ===")
    settings = get_settings()
    mem = AgentMemory(settings.chroma_host, settings.chroma_port)
    role = "backend_agent"
    pid = "00000000-0000-0000-0000-000000000001"
    lessons_data = [
        (
            "Booking API threw errors on date inputs",
            "Timezone handling inconsistent, mixing local and UTC",
            "Rewrote date logic to enforce UTC throughout",
            "Always convert all dates to UTC at the API boundary",
        ),
        (
            "Auth API returned 500 on empty password field",
            "No input validation before database query",
            "Added input validation layer before all DB calls",
            "Validate all inputs before touching the database",
        ),
        (
            "Payment API double-charged on network timeout",
            "No idempotency key on payment requests",
            "Added idempotency keys to all payment endpoints",
            "All payment endpoints must use idempotency keys",
        ),
    ]
    for i, (fd, rc, res, rule) in enumerate(lessons_data):
        lid = new_lesson_id()
        tid = f"00000000-0000-0000-0000-0000000000{i + 2:02d}"
        lesson = Lesson(
            id=lid,
            agent_role=role,
            failure_description=fd,
            root_cause=rc,
            resolution=res,
            rule=rule,
            created_at=datetime.now(UTC),
            project_id=pid,
            task_id=tid,
        )
        await mem.write_lesson(lesson)
        short = fd[:40] + ("…" if len(fd) > 40 else "")
        print(f"[MEMORY] Lesson written for {role}: {short}")

    q = "Build a reservation API that handles booking dates and timezones"
    print(f"[MEMORY] Querying: {q}")
    ranked = await mem.retrieve_lessons(role, q, top_k=3)
    print("[MEMORY] Top 3 lessons retrieved:")
    for i, item in enumerate(ranked, start=1):
        print(f"  {i}. [{item.relevance_score:.2f}] {item.lesson.rule}")
    print()


async def _run_checkpoint_demo(session: AsyncSession) -> None:
    print("=== RUN 3: TASK CHECKPOINT ===")
    settings = get_settings()
    tc = TaskCheckpoint(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_bucket,
        secure=settings.minio_secure,
    )
    tm = TaskMemory(settings.redis_url, ttl_seconds=settings.task_memory_ttl)
    lead = LeadAgent("lead_agent_1", session, task_memory=tm)
    backend = BackendAgent("backend_agent_1", session, task_memory=tm)

    task = await lead.create_task("Checkpoint task", None, TaskComplexity.LOW, "backend_agent_1")
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    tid = str(task.id)
    aid = "backend_agent_1"
    path = await tc.save(tid, aid, {"progress": "50%", "last_step": "schema defined"})
    print(f"[CHECKPOINT] Saved: {path}")
    loaded = await tc.load(path)
    print(f"[CHECKPOINT] Loaded: {loaded}")
    latest = await tc.get_latest(tid, aid)
    print(f"[CHECKPOINT] Latest matches loaded: {latest == loaded}")
    qa = QAAgent(
        "qa_agent_1",
        session,
        test_runner=_make_runner(TaskComplexity.LOW),
        task_memory=tm,
    )
    await backend.complete_work(task.id, output="done")
    await qa.begin_review(task.id)
    await qa.approve(task.id, output="done")
    await tc.delete(tid)
    print("[CHECKPOINT] Deleted on DONE")
    print()


async def _run_escalation_persistence_demo(session: AsyncSession) -> None:
    print("=== RUN 4: ESCALATION PERSISTENCE ===")
    settings = get_settings()
    loop_counter = LoopCounter(settings.redis_url)
    persistence = EscalationPersistence(session)
    ladder = EscalationLadder(
        loop_counter=loop_counter,
        persistence=persistence,
        max_self_retries=settings.max_self_retries,
    )

    lead = LeadAgent("lead_agent_1", session)
    backend = BackendAgent("backend_agent_1", session)
    qa = QAAgent("qa_agent_1", session, test_runner=_make_runner(TaskComplexity.HIGH))

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
            if attempt == 3 and await loop_counter.should_escalate(
                task_id, "test_failure:assertion_error"
            ):
                print("[ESCALATION] Same error seen 3 times — skipping Level 1, jumping to Level 2")
            if result.needs_human_input:
                print("[ESCALATION] Level 5: Human input required")
                print(f"[FORGEAI] [WARNING] Task needs human input: {result.human_message}")
            break
        except AlreadyEscalatedError:
            if attempt < 3:
                continue
            print("[FORGEAI] Task already at Level 5 and blocked until human input.")

    result_db = await session.execute(
        select(EscalationEventModel).where(EscalationEventModel.task_id == task.id)
    )
    rows = result_db.scalars().all()
    print("[DB] Escalation events for task:")
    for i, row in enumerate(rows, start=1):
        extra = f" | needs_human_input={row.needs_human_input}" if row.needs_human_input else ""
        print(
            f"  {i}. level={row.level} | error={row.error_signature} | "
            f"resolved={row.resolved}{extra}"
        )
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
    print("=== DRIFT DETECTION (Phase 3 carry-over) ===")
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
        settings = get_settings()
        async with AsyncSessionFactory() as session:
            await _run_task_memory_path(session)
            await _run_lesson_demo()
            await _run_checkpoint_demo(session)
            await _run_escalation_persistence_demo(session)
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
