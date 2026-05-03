"""Tests for ``TaskStateMachine``: permitted edges, rejections, conditions, audit."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.exceptions import InvalidTransitionError, TransitionConditionError
from forgeai.models.task import Task, TaskComplexity, TaskStateHistory
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import (
    KEY_DEFECT_REPORT,
    KEY_OUTPUT,
    KEY_PHASE_APPROVAL,
    KEY_REWORK_REASON,
    KEY_WORK_OUTPUT,
)

pytestmark = pytest.mark.asyncio


async def _task_in_state(
    session: AsyncSession,
    state: TaskState,
    *,
    assigned: str = "backend_fixture",
) -> Task:
    """Build a persisted task whose ``current_state`` is ``state``."""
    if state == TaskState.PHASE_LOCKED:
        lead = LeadAgent("lead_sm", session)
        return await lead.create_task(
            "sm", None, TaskComplexity.LOW, assigned_agent=assigned
        )
    lead = LeadAgent("lead_sm", session)
    backend = BackendAgent(assigned, session)
    qa = QAAgent("qa_sm", session)
    task = await lead.create_task(
        "sm", None, TaskComplexity.LOW, assigned_agent=assigned
    )
    if state == TaskState.TODO:
        await lead.approve_phase_transition(task.id)
        await session.refresh(task)
        return task
    if state == TaskState.IN_PROGRESS:
        await lead.approve_phase_transition(task.id)
        await lead.assign_task(task.id)
        await session.refresh(task)
        return task
    if state == TaskState.IN_REVIEW:
        await lead.approve_phase_transition(task.id)
        await lead.assign_task(task.id)
        await backend.complete_work(task.id, output="out")
        await session.refresh(task)
        return task
    if state == TaskState.TESTING:
        await lead.approve_phase_transition(task.id)
        await lead.assign_task(task.id)
        await backend.complete_work(task.id, output="out")
        await qa.begin_review(task.id)
        await session.refresh(task)
        return task
    if state == TaskState.DONE:
        await lead.approve_phase_transition(task.id)
        await lead.assign_task(task.id)
        await backend.complete_work(task.id, output="done output")
        await qa.begin_review(task.id)
        await qa.approve(task.id)
        await session.refresh(task)
        return task
    if state == TaskState.REWORK:
        done_task = await _task_in_state(session, TaskState.DONE, assigned=assigned)
        machine = TaskStateMachine(session)
        await machine.transition(
            done_task.id,
            TaskState.REWORK,
            "agent_sm",
            **{KEY_REWORK_REASON: "needs rework"},
        )
        await session.refresh(done_task)
        return done_task
    raise AssertionError(f"unsupported fixture state: {state}")


@pytest.mark.parametrize(
    ("from_state", "to_state", "kwargs"),
    [
        (TaskState.PHASE_LOCKED, TaskState.TODO, {KEY_PHASE_APPROVAL: True}),
        (TaskState.TODO, TaskState.IN_PROGRESS, {}),
        (TaskState.IN_PROGRESS, TaskState.IN_REVIEW, {KEY_WORK_OUTPUT: "w"}),
        (TaskState.IN_REVIEW, TaskState.TESTING, {}),
        (TaskState.TESTING, TaskState.DONE, {KEY_OUTPUT: "final"}),
        (
            TaskState.TESTING,
            TaskState.IN_PROGRESS,
            {KEY_DEFECT_REPORT: "bug here"},
        ),
    ],
)
async def test_permitted_transitions_succeed(
    db_session: AsyncSession,
    from_state: TaskState,
    to_state: TaskState,
    kwargs: dict,
) -> None:
    """Each permitted edge succeeds and updates ``current_state``."""
    task = await _task_in_state(db_session, from_state)
    machine = TaskStateMachine(db_session)
    updated = await machine.transition(
        task.id,
        to_state,
        "agent_sm",
        **kwargs,
    )
    assert updated.current_state == to_state


async def test_permitted_done_to_rework(db_session: AsyncSession) -> None:
    """``DONE`` → ``REWORK`` succeeds with ``rework_reason``."""
    task = await _task_in_state(db_session, TaskState.DONE)
    machine = TaskStateMachine(db_session)
    updated = await machine.transition(
        task.id,
        TaskState.REWORK,
        "agent_sm",
        **{KEY_REWORK_REASON: "scope changed"},
    )
    assert updated.current_state == TaskState.REWORK


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (TaskState.TODO, TaskState.DONE),
        (TaskState.IN_PROGRESS, TaskState.DONE),
        (TaskState.DONE, TaskState.IN_PROGRESS),
        (TaskState.TESTING, TaskState.TODO),
        (TaskState.IN_REVIEW, TaskState.IN_PROGRESS),
        (TaskState.PHASE_LOCKED, TaskState.IN_PROGRESS),
        (TaskState.REWORK, TaskState.DONE),
    ],
)
async def test_invalid_transition_raises(
    db_session: AsyncSession,
    from_state: TaskState,
    to_state: TaskState,
) -> None:
    """Disallowed edges raise ``InvalidTransitionError`` and leave state unchanged."""
    task = await _task_in_state(db_session, from_state)
    prior = task.current_state
    machine = TaskStateMachine(db_session)
    with pytest.raises(InvalidTransitionError):
        await machine.transition(task.id, to_state, "agent_sm")
    await db_session.refresh(task)
    assert task.current_state == prior


async def test_phase_locked_to_todo_without_approval_raises(
    db_session: AsyncSession,
    locked_task: Task,
) -> None:
    """``PHASE_LOCKED`` → ``TODO`` without approval is a condition failure."""
    machine = TaskStateMachine(db_session)
    with pytest.raises(TransitionConditionError):
        await machine.transition(
            locked_task.id,
            TaskState.TODO,
            "agent_sm",
        )


async def test_testing_to_in_progress_without_defect_raises(
    db_session: AsyncSession,
    task_at_testing: Task,
) -> None:
    """``TESTING`` → ``IN_PROGRESS`` requires a non-empty ``defect_report``."""
    machine = TaskStateMachine(db_session)
    with pytest.raises(TransitionConditionError):
        await machine.transition(
            task_at_testing.id,
            TaskState.IN_PROGRESS,
            "agent_sm",
        )


async def test_done_without_output_raises(
    db_session: AsyncSession,
    task_at_testing: Task,
) -> None:
    """``TESTING`` → ``DONE`` requires non-empty ``output``."""
    machine = TaskStateMachine(db_session)
    with pytest.raises(TransitionConditionError):
        await machine.transition(
            task_at_testing.id,
            TaskState.DONE,
            "agent_sm",
        )


async def test_successful_transition_writes_history_row(
    db_session: AsyncSession,
    locked_task: Task,
) -> None:
    """A successful transition persists a ``TaskStateHistory`` row with success."""
    machine = TaskStateMachine(db_session)
    await machine.transition(
        locked_task.id,
        TaskState.TODO,
        "agent_sm",
        **{KEY_PHASE_APPROVAL: True},
    )
    hist = await machine.get_history(locked_task.id)
    assert len(hist) == 1
    assert hist[0].success is True
    assert hist[0].from_state == TaskState.PHASE_LOCKED
    assert hist[0].to_state == TaskState.TODO


async def test_failed_transition_writes_history_row(
    db_session: AsyncSession,
    locked_task: Task,
) -> None:
    """A rejected transition persists a failed audit row."""
    machine = TaskStateMachine(db_session)
    with pytest.raises(InvalidTransitionError):
        await machine.transition(
            locked_task.id,
            TaskState.DONE,
            "agent_sm",
        )
    hist = await machine.get_history(locked_task.id)
    assert len(hist) == 1
    assert hist[0].success is False
    assert hist[0].rejection_reason is not None


async def test_get_history_chronological_order(
    db_session: AsyncSession,
    locked_task: Task,
) -> None:
    """``get_history`` returns rows oldest-first (increasing ``attempted_at``)."""
    machine = TaskStateMachine(db_session)
    await machine.transition(
        locked_task.id,
        TaskState.TODO,
        "a1",
        **{KEY_PHASE_APPROVAL: True},
    )
    await machine.transition(
        locked_task.id,
        TaskState.IN_PROGRESS,
        "a2",
    )
    await machine.transition(
        locked_task.id,
        TaskState.IN_REVIEW,
        "a3",
        **{KEY_WORK_OUTPUT: "x"},
    )
    hist = await machine.get_history(locked_task.id)
    assert len(hist) == 3
    times = [row.attempted_at for row in hist]
    assert times == sorted(times)
    assert [h.from_state for h in hist] == [
        TaskState.PHASE_LOCKED,
        TaskState.TODO,
        TaskState.IN_PROGRESS,
    ]


async def test_condition_failure_writes_failed_history(
    db_session: AsyncSession,
    locked_task: Task,
) -> None:
    """Failed condition attempts append ``success=False`` history."""
    machine = TaskStateMachine(db_session)
    with pytest.raises(TransitionConditionError):
        await machine.transition(locked_task.id, TaskState.TODO, "agent_sm")
    hist = await machine.get_history(locked_task.id)
    assert len(hist) == 1
    assert hist[0].success is False
