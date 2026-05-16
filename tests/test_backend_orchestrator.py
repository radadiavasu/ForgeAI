"""BackendOrchestrator tests — mocked LLM and Sandbox."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.escalation import EscalationLadder, EscalationPersistence
from forgeai.escalation.loop_counter import LoopCounter
from forgeai.llm.schemas import LLMResponse
from forgeai.memory.task_memory import TaskMemory
from forgeai.models.task import Task, TaskComplexity
from forgeai.orchestration.backend_orchestrator import BackendOrchestrator, ContractValidator
from forgeai.orchestration.qa_loop import QAOrchestrator
from forgeai.orchestration.schemas import BackendPhaseResult
from forgeai.sandbox.schemas import RunnerOutput, SandboxTestCaseResult
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState


def _contract() -> dict:
    return {"endpoints": [{"method": "GET", "path": "/tasks"}]}


def _backend_code() -> str:
    return json.dumps(
        {
            "code": "def list_tasks():\n    return []\n",
            "test_code": "from main import list_tasks\n\ndef test_list():\n    assert list_tasks() == []\n",
        }
    )


def _validation_json(*, valid: bool) -> str:
    return json.dumps({"valid": valid, "violations": [] if valid else ["bad"], "severity": "blocking"})


def _defect_json() -> str:
    return json.dumps(
        {
            "failure_summary": "tests failed",
            "suggestions": "fix implementation",
            "failed_tests": ["test_list"],
            "passed_tests": [],
        }
    )


def _llm(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model_used="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )


@pytest.fixture
def task_memory() -> TaskMemory:
    return TaskMemory("redis://localhost:6379/0", ttl_seconds=3600)


@pytest.fixture
def mock_llm() -> AsyncMock:
    llm = AsyncMock()
    return llm


@pytest.fixture
def mock_runner() -> MagicMock:
    runner = MagicMock()
    runner.run = AsyncMock(
        return_value=RunnerOutput(
            success=True,
            total_tests=2,
            passed_tests=2,
            failed_tests=0,
            test_cases=[SandboxTestCaseResult(name="test_list", passed=True)],
            stdout="ok",
            stderr="",
            execution_time_seconds=0.1,
        )
    )
    return runner


async def _create_backend_tasks(
    db_session: AsyncSession,
    project_id: uuid.UUID,
    count: int = 2,
) -> list[Task]:
    lead = LeadAgent("lead_1", db_session)
    tasks: list[Task] = []
    for i in range(count):
        t = await lead.create_task(
            f"Backend endpoint {i + 1}",
            f"Implement endpoint {i + 1}",
            TaskComplexity.LOW,
            "backend_agent_1",
            project_id=project_id,
        )
        await lead.approve_phase_transition(t.id)
        tasks.append(t)
    return tasks


def _build_orchestrator(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_runner: MagicMock,
    task_memory: TaskMemory,
) -> BackendOrchestrator:
    lead = LeadAgent("lead_1", db_session, task_memory=task_memory, llm_client=mock_llm)
    backend = BackendAgent(
        "backend_agent_1",
        db_session,
        task_memory=task_memory,
        llm_client=mock_llm,
        agent_memory=MagicMock(),
    )
    backend.memory.retrieve_lessons = AsyncMock(return_value=[])
    validator = ContractValidator(mock_llm)
    qa = QAAgent(
        "qa_agent_1",
        db_session,
        test_runner=mock_runner,
        task_memory=task_memory,
        llm_client=mock_llm,
        contract_validator=validator,
    )
    loop_counter = LoopCounter()
    ladder = EscalationLadder(loop_counter, EscalationPersistence(db_session))
    sm = TaskStateMachine(db_session, task_memory=task_memory)
    qa_orch = QAOrchestrator(sm, loop_counter, ladder, mock_llm, db_session, task_memory=task_memory)
    return BackendOrchestrator(
        lead,
        backend,
        qa,
        qa_orch,
        validator,
        db_session,
        loop_counter=loop_counter,
        escalation_ladder=ladder,
    )


@pytest.mark.asyncio
async def test_run_backend_phase_processes_all_todo_tasks(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_runner: MagicMock,
    task_memory: TaskMemory,
) -> None:
    mock_llm.complete.side_effect = [
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
    ]
    project_id = uuid.uuid4()
    await _create_backend_tasks(db_session, project_id, count=2)
    orch = _build_orchestrator(db_session, mock_llm, mock_runner, task_memory)
    result = await orch.run_backend_phase(str(project_id), _contract())
    assert isinstance(result, BackendPhaseResult)
    assert result.total_tasks == 2
    assert len(result.completed_tasks) == 2


@pytest.mark.asyncio
async def test_tasks_processed_sequentially(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_runner: MagicMock,
    task_memory: TaskMemory,
) -> None:
    mock_llm.complete.side_effect = [
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
    ]
    project_id = uuid.uuid4()
    await _create_backend_tasks(db_session, project_id, count=2)
    orch = _build_orchestrator(db_session, mock_llm, mock_runner, task_memory)
    await orch.run_backend_phase(str(project_id), _contract())
    assert mock_runner.run.call_count == 2


@pytest.mark.asyncio
async def test_api_contract_passed_to_backend_agent(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_runner: MagicMock,
    task_memory: TaskMemory,
) -> None:
    mock_llm.complete.side_effect = [
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
    ]
    project_id = uuid.uuid4()
    await _create_backend_tasks(db_session, project_id, count=1)
    orch = _build_orchestrator(db_session, mock_llm, mock_runner, task_memory)
    await orch.run_backend_phase(str(project_id), _contract())
    first_call = mock_llm.complete.await_args_list[0]
    assert "API_Contract" in first_call.kwargs.get("user_message", first_call.args[1] if len(first_call.args) > 1 else "")


@pytest.mark.asyncio
async def test_backend_phase_result_task_count(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_runner: MagicMock,
    task_memory: TaskMemory,
) -> None:
    mock_llm.complete.side_effect = [
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
    ]
    project_id = uuid.uuid4()
    await _create_backend_tasks(db_session, project_id, count=1)
    orch = _build_orchestrator(db_session, mock_llm, mock_runner, task_memory)
    result = await orch.run_backend_phase(str(project_id), _contract())
    assert result.total_tasks == 1
    assert len(result.completed_tasks) == 1


@pytest.mark.asyncio
async def test_backend_phase_result_tracks_qa_cycles(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_runner: MagicMock,
    task_memory: TaskMemory,
) -> None:
    mock_llm.complete.side_effect = [
        _llm(_backend_code()),
        _llm(_validation_json(valid=False)),
        _llm(_defect_json()),
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
    ]
    project_id = uuid.uuid4()
    await _create_backend_tasks(db_session, project_id, count=1)
    orch = _build_orchestrator(db_session, mock_llm, mock_runner, task_memory)
    result = await orch.run_backend_phase(str(project_id), _contract())
    assert result.qa_cycles >= 2


@pytest.mark.asyncio
async def test_backend_phase_result_tracks_contract_violations(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_runner: MagicMock,
    task_memory: TaskMemory,
) -> None:
    mock_llm.complete.side_effect = [
        _llm(_backend_code()),
        _llm(_validation_json(valid=False)),
        _llm(_defect_json()),
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
    ]
    project_id = uuid.uuid4()
    await _create_backend_tasks(db_session, project_id, count=1)
    orch = _build_orchestrator(db_session, mock_llm, mock_runner, task_memory)
    result = await orch.run_backend_phase(str(project_id), _contract())
    assert result.contract_violations_caught >= 1


@pytest.mark.asyncio
async def test_rejection_loop_for_backend_tasks(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_runner: MagicMock,
    task_memory: TaskMemory,
) -> None:
    mock_runner.run.side_effect = [
        RunnerOutput(
            success=False,
            total_tests=1,
            passed_tests=0,
            failed_tests=1,
            test_cases=[SandboxTestCaseResult(name="test_list", passed=False)],
            stdout="",
            stderr="fail",
            execution_time_seconds=0.1,
        ),
        RunnerOutput(
            success=True,
            total_tests=1,
            passed_tests=1,
            failed_tests=0,
            test_cases=[SandboxTestCaseResult(name="test_list", passed=True)],
            stdout="ok",
            stderr="",
            execution_time_seconds=0.1,
        ),
    ]
    mock_llm.complete.side_effect = [
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
        _llm(_defect_json()),
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
    ]
    project_id = uuid.uuid4()
    tasks = await _create_backend_tasks(db_session, project_id, count=1)
    orch = _build_orchestrator(db_session, mock_llm, mock_runner, task_memory)
    result = await orch.run_backend_phase(str(project_id), _contract())
    assert len(result.completed_tasks) == 1
    await db_session.refresh(tasks[0])
    assert tasks[0].current_state == TaskState.DONE


@pytest.mark.asyncio
async def test_escalation_when_loop_counter_reaches_three(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_runner: MagicMock,
    task_memory: TaskMemory,
) -> None:
    mock_runner.run.return_value = RunnerOutput(
        success=False,
        total_tests=1,
        passed_tests=0,
        failed_tests=1,
        test_cases=[SandboxTestCaseResult(name="t", passed=False)],
        stdout="",
        stderr="fail",
        execution_time_seconds=0.1,
    )
    responses = [
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
        _llm(_defect_json()),
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
        _llm(_defect_json()),
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
        _llm(_defect_json()),
        _llm(_backend_code()),
        _llm(_validation_json(valid=True)),
    ]

    def _next_llm(*_args, **_kwargs):  # noqa: ANN002
        if responses:
            return responses.pop(0)
        return _llm(_defect_json())

    mock_llm.complete.side_effect = _next_llm
    project_id = uuid.uuid4()
    await _create_backend_tasks(db_session, project_id, count=1)
    orch = _build_orchestrator(db_session, mock_llm, mock_runner, task_memory)
    result = await orch.run_backend_phase(str(project_id), _contract())
    assert result.escalations >= 1


@pytest.mark.asyncio
async def test_phase_advances_to_final_review_after_gate_approval(
    db_session: AsyncSession,
) -> None:
    mock_llm = AsyncMock()
    lead = LeadAgent("lead_1", db_session, llm_client=mock_llm)
    project_id = uuid.uuid4()
    backend_result = BackendPhaseResult(
        project_id=str(project_id),
        completed_tasks=[],
        total_tasks=1,
        qa_cycles=1,
        contract_violations_caught=0,
    )

    async def _approve(_s: str) -> bool:
        return True

    result = await lead.execute_backend_gate(backend_result, project_id, _approve)
    assert result.approved is True
    from forgeai.models.project_artefact import ProjectArtefactModel

    row = (
        await db_session.execute(
            select(ProjectArtefactModel).where(
                ProjectArtefactModel.project_id == project_id,
                ProjectArtefactModel.artefact_type == "current_phase",
                ProjectArtefactModel.is_current.is_(True),
            )
        )
    ).scalar_one()
    assert row.content.get("phase") == "FINAL_REVIEW"
