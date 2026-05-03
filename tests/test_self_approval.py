"""QA must not approve work produced under the same ``agent_id``."""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.exceptions import SelfApprovalError
from forgeai.models.task import Task, TaskComplexity
from forgeai.state_machine.states import TaskState

pytestmark = pytest.mark.asyncio

_SHARED_AGENT_ID = "colliding_agent"


@pytest_asyncio.fixture
async def task_in_review_same_implementer(db_session: AsyncSession) -> Task:
    """Task in ``IN_REVIEW`` where implementer id equals ``_SHARED_AGENT_ID``."""
    lead = LeadAgent("lead_only", db_session)
    backend = BackendAgent(_SHARED_AGENT_ID, db_session)
    task = await lead.create_task(
        title="Self-approval scenario",
        description=None,
        complexity=TaskComplexity.HIGH,
        assigned_agent=_SHARED_AGENT_ID,
    )
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    await backend.complete_work(task.id, output="my own work")
    await db_session.refresh(task)
    assert task.current_state == TaskState.IN_REVIEW
    return task


async def test_qa_cannot_approve_own_implementation(
    db_session: AsyncSession,
    task_in_review_same_implementer: Task,
) -> None:
    """Same ``agent_id`` as backend raises ``SelfApprovalError``; state stays ``IN_REVIEW``."""
    task_id = task_in_review_same_implementer.id
    qa = QAAgent(_SHARED_AGENT_ID, db_session)

    with pytest.raises(SelfApprovalError):
        await qa.approve(task_id)

    result = await db_session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one()
    assert task.current_state == TaskState.IN_REVIEW
