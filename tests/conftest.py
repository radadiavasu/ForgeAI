"""Pytest fixtures: async DB session, schema reset, and sample tasks."""

import forgeai.models.escalation  # noqa: F401 — register metadata for create_all

import fakeredis.aioredis
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.database import AsyncSessionFactory, engine
from forgeai.models.task import Base, Task, TaskComplexity
from forgeai.state_machine.states import TaskState


@pytest.fixture(autouse=True)
def _shared_fake_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use in-memory Redis so LoopCounter and TaskMemory tests need no daemon."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    def _from_url(*_args, **_kwargs):  # noqa: ANN002
        return fake

    monkeypatch.setattr("redis.asyncio.from_url", _from_url)


@pytest_asyncio.fixture(autouse=True)
async def reset_database() -> None:
    """Drop and recreate all tables before each test for isolation."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Global async engine pools connections on the test's event loop; dispose so the
    # next test's loop does not inherit a closed connection (pytest-asyncio strict).
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Provide an async SQLAlchemy session bound to the test engine."""
    async with AsyncSessionFactory() as session:
        yield session


@pytest_asyncio.fixture
async def locked_task(db_session: AsyncSession) -> Task:
    """Create and persist a task in ``PHASE_LOCKED`` via ``LeadAgent``."""
    lead = LeadAgent("lead_fixture", db_session)
    return await lead.create_task(
        title="Fixture task",
        description=None,
        complexity=TaskComplexity.LOW,
        assigned_agent="backend_fixture",
    )


@pytest_asyncio.fixture
async def task_at_testing(db_session: AsyncSession) -> Task:
    """Task progressed through ``IN_REVIEW`` into ``TESTING`` (happy path prefix)."""
    lead = LeadAgent("lead_fixture", db_session)
    backend = BackendAgent("backend_fixture", db_session)
    qa = QAAgent("qa_fixture", db_session)
    task = await lead.create_task(
        title="Testing-state task",
        description=None,
        complexity=TaskComplexity.MEDIUM,
        assigned_agent="backend_fixture",
    )
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    await backend.complete_work(task.id, output="fixture output for QA")
    await qa.begin_review(task.id)
    await db_session.refresh(task)
    assert task.current_state == TaskState.TESTING
    return task
