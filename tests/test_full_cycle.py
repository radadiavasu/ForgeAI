"""End-to-end lifecycle: same path as ``main.py`` but asserted in tests."""

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.models.task import Task, TaskComplexity, TaskStateHistory
from forgeai.state_machine.states import TaskState

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def cycled_task(db_session: AsyncSession) -> Task:
    """Run the full happy-path lifecycle and return the final task row."""
    lead = LeadAgent("lead_agent_1", db_session)
    backend = BackendAgent("backend_agent_1", db_session)
    qa = QAAgent("qa_agent_1", db_session)

    task = await lead.create_task(
        title="Build Auth API",
        description=None,
        complexity=TaskComplexity.MEDIUM,
        assigned_agent="backend_agent_1",
    )
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    await backend.complete_work(task.id, output="JWT auth implemented")
    await qa.begin_review(task.id)
    await qa.approve(task.id)
    await db_session.refresh(task)
    return task


async def test_full_cycle_final_state_and_history(
    db_session: AsyncSession,
    cycled_task: Task,
) -> None:
    """After the scripted cycle, task is ``DONE`` with five success history rows."""
    assert cycled_task.current_state == TaskState.DONE
    assert cycled_task.output is not None
    assert cycled_task.output.strip()

    count = await db_session.scalar(
        select(func.count()).select_from(TaskStateHistory).where(
            TaskStateHistory.task_id == cycled_task.id
        )
    )
    assert count == 5

    result = await db_session.execute(
        select(TaskStateHistory)
        .where(TaskStateHistory.task_id == cycled_task.id)
        .order_by(TaskStateHistory.attempted_at.asc())
    )
    rows = list(result.scalars().all())
    assert len(rows) == 5
    assert all(r.success for r in rows)
