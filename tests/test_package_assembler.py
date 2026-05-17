"""PackageAssembler tests — mocked LLM, Git, and QA."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.lead_agent import LeadAgent
from forgeai.delivery.git_manager import GitManager
from forgeai.delivery.package_assembler import PackageAssembler
from forgeai.delivery.schemas import RollbackPoint
from forgeai.llm.schemas import (
    APISurface,
    Component,
    LLMResponse,
    MasterDocument,
    TechStack,
    TechStackDocument,
)
from forgeai.models.task import TaskComplexity
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_OUTPUT, KEY_WORK_OUTPUT


def _master() -> MasterDocument:
    return MasterDocument(
        project_name="Task Manager",
        project_summary="Personal tasks",
        components=[
            Component(
                name="Dashboard",
                responsibility="UI",
                dependencies=[],
                acceptance_criteria=["renders"],
            )
        ],
        api_surfaces=[
            APISurface(
                endpoint="/tasks",
                method="GET",
                request_schema={},
                response_schema={},
                description="list",
            )
        ],
        tech_stack=TechStack(
            language="Python",
            framework="React",
            database="PostgreSQL",
            testing_framework="pytest",
            rationale="r",
            rejected_alternatives=[],
        ),
    )


def _tech_doc() -> TechStackDocument:
    return TechStackDocument(
        language="Python",
        framework="React",
        database="PostgreSQL",
        testing_framework="pytest",
        rationale="r",
        rejected_alternatives=[],
    )


@pytest.fixture
def mock_llm() -> AsyncMock:
    llm = AsyncMock()

    async def _complete(**kwargs):
        prompt = kwargs.get("system_prompt", "")
        if "Dockerfile" in prompt:
            body = "FROM python:3.11-slim\nWORKDIR /app\n"
        else:
            body = "services:\n  app:\n    build: .\n"
        return LLMResponse(
            content=body,
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.0,
        )

    llm.complete.side_effect = _complete
    return llm


def _git_mocks() -> MagicMock:
    git = MagicMock(spec=GitManager)
    git.init_repo = MagicMock()
    git.commit = MagicMock(return_value=MagicMock(hash="abc123456789"))
    rollback = RollbackPoint(
        tag_name="release-v1",
        message="ForgeAI delivery release v1",
        created_at=datetime.now(UTC),
        commit_hash="abc123456789",
    )
    git.create_tag = MagicMock(return_value=rollback)
    git.get_log = MagicMock(return_value=[])
    git.get_tags = MagicMock(return_value=[rollback])
    return git


@pytest.fixture
def mock_qa() -> AsyncMock:
    qa = AsyncMock()
    qa.validate_docker_build = AsyncMock(return_value=True)
    return qa


@pytest.mark.asyncio
async def test_assemble_creates_output_directory(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_qa: AsyncMock,
    tmp_path: Path,
) -> None:
    git = _git_mocks()
    asm = PackageAssembler(db_session, git, mock_qa, mock_llm)
    out = tmp_path / "pkg"
    project_id = str(uuid.uuid4())
    pkg = await asm.assemble(project_id, _master(), _tech_doc(), str(out))
    assert out.is_dir()
    assert pkg.output_dir


@pytest.mark.asyncio
async def test_assemble_writes_files_for_done_tasks(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_qa: AsyncMock,
    tmp_path: Path,
) -> None:
    lead = LeadAgent("lead_1", db_session)
    project_id = uuid.uuid4()
    task = await lead.create_task(
        "Build Dashboard page",
        "UI page",
        TaskComplexity.LOW,
        "frontend_agent_1",
        project_id=project_id,
    )
    await lead.approve_phase_transition(task.id)
    sm = TaskStateMachine(db_session)
    await sm.transition(task.id, TaskState.IN_PROGRESS, "frontend_agent_1")
    await sm.transition(
        task.id,
        TaskState.IN_REVIEW,
        "frontend_agent_1",
        **{KEY_WORK_OUTPUT: "export default function Dashboard() {}"},
    )
    await sm.transition(task.id, TaskState.TESTING, "qa_1")
    await sm.transition(
        task.id,
        TaskState.DONE,
        "qa_1",
        **{KEY_OUTPUT: "QA approved"},
    )

    git = _git_mocks()

    with patch(
        "forgeai.delivery.package_assembler.ReadmeGenerator.generate",
        new=AsyncMock(return_value="# App\n\n## Setup\n1. a\n2. docker compose up\n3. b"),
    ):
        asm = PackageAssembler(db_session, git, mock_qa, mock_llm)
        pkg = await asm.assemble(
            str(project_id), _master(), _tech_doc(), str(tmp_path / "out")
        )
    assert "src/pages/Dashboard.jsx" in pkg.files_written
    assert (tmp_path / "out" / "src" / "pages" / "Dashboard.jsx").is_file()


def test_derive_file_path_frontend_page() -> None:
    asm = PackageAssembler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    path = asm._derive_file_path(
        "Build Dashboard page",
        "frontend_page",
        _tech_doc(),
    )
    assert path == "src/pages/Dashboard.jsx"


def test_derive_file_path_navbar_component() -> None:
    asm = PackageAssembler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    path = asm._derive_file_path(
        "Build NavBar component",
        "frontend_component",
        _tech_doc(),
    )
    assert path == "src/components/NavBar.jsx"


def test_derive_file_path_rest_api_for_tasks() -> None:
    asm = PackageAssembler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    path = asm._derive_file_path(
        "REST API for tasks",
        "backend_api",
        _tech_doc(),
    )
    assert path == "src/api/tasks.py"


def test_derive_file_path_backend_task_number_fallback() -> None:
    asm = PackageAssembler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    path = asm._derive_file_path(
        "Backend task 3",
        "backend_api",
        _tech_doc(),
    )
    assert path == "src/api/backend_module_3.py"


@pytest.mark.asyncio
async def test_task_output_prefers_work_output_over_qa_placeholder(
    db_session: AsyncSession,
) -> None:
    lead = LeadAgent("lead_1", db_session)
    project_id = uuid.uuid4()
    task = await lead.create_task(
        "Build Settings page",
        None,
        TaskComplexity.LOW,
        "frontend_agent_2",
        project_id=project_id,
    )
    await lead.approve_phase_transition(task.id)
    sm = TaskStateMachine(db_session)
    react = "export default function Settings() { return null; }"
    await sm.transition(task.id, TaskState.IN_PROGRESS, "frontend_agent_2")
    await sm.transition(
        task.id,
        TaskState.IN_REVIEW,
        "frontend_agent_2",
        **{KEY_WORK_OUTPUT: react},
    )
    await sm.transition(task.id, TaskState.TESTING, "qa_1")
    await sm.transition(task.id, TaskState.DONE, "qa_1", **{KEY_OUTPUT: "QA approved"})
    asm = PackageAssembler(db_session, MagicMock(), MagicMock(), MagicMock())
    out = await asm._resolve_write_content(task)
    assert "Settings" in out


@pytest.mark.asyncio
async def test_assemble_writes_placeholder_when_no_output(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_qa: AsyncMock,
    tmp_path: Path,
) -> None:
    lead = LeadAgent("lead_1", db_session)
    project_id = uuid.uuid4()
    task = await lead.create_task(
        "Backend task 9",
        None,
        TaskComplexity.LOW,
        "backend_agent_1",
        project_id=project_id,
    )
    await lead.approve_phase_transition(task.id)
    sm = TaskStateMachine(db_session)
    await sm.transition(task.id, TaskState.IN_PROGRESS, "backend_agent_1")
    await sm.transition(task.id, TaskState.IN_REVIEW, "backend_agent_1")
    await sm.transition(task.id, TaskState.TESTING, "qa_1")
    await sm.transition(task.id, TaskState.DONE, "qa_1", **{KEY_OUTPUT: "ok"})

    git = _git_mocks()
    with patch(
        "forgeai.delivery.package_assembler.ReadmeGenerator.generate",
        new=AsyncMock(return_value="# App\n\n## Setup\n1. a\n2. docker compose up\n3. b"),
    ):
        asm = PackageAssembler(db_session, git, mock_qa, mock_llm)
        pkg = await asm.assemble(
            str(project_id), _master(), _tech_doc(), str(tmp_path / "empty_out")
        )
    target = tmp_path / "empty_out" / "src" / "api" / "backend_module_9.py"
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert "Backend task 9" in text
    assert "src/api/backend_module_9.py" in pkg.files_written


@pytest.mark.asyncio
async def test_generate_dockerfile_python_functional(tmp_path: Path) -> None:
    asm = PackageAssembler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    root = tmp_path / "pyonly"
    (root / "src" / "api").mkdir(parents=True)
    (root / "src" / "api" / "tasks.py").write_text("# api\n", encoding="utf-8")
    content = await asm._generate_dockerfile(
        _tech_doc(), str(root), has_frontend=False, has_backend=True
    )
    assert "FROM python:3.11-slim" in content
    assert "pip install" in content
    assert "requirements.txt" in content
    assert "placeholder" not in content.lower()
    assert 'CMD ["python", "src/api/main.py"]' in content


@pytest.mark.asyncio
async def test_generate_dockerfile_react_python_multi_stage(tmp_path: Path) -> None:
    asm = PackageAssembler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    root = tmp_path / "full"
    (root / "src" / "pages").mkdir(parents=True)
    (root / "src" / "api").mkdir(parents=True)
    (root / "src" / "pages" / "Dashboard.jsx").write_text("export default () => null\n")
    (root / "src" / "api" / "tasks.py").write_text("# api\n", encoding="utf-8")
    content = await asm._generate_dockerfile(
        _tech_doc(), str(root), has_frontend=True, has_backend=True
    )
    assert "AS frontend-build" in content
    assert "node:20-alpine" in content
    assert "python:3.11-slim" in content


@pytest.mark.asyncio
async def test_generate_docker_compose_low_complexity(mock_llm: AsyncMock) -> None:
    asm = PackageAssembler(MagicMock(), MagicMock(), MagicMock(), mock_llm)
    await asm._generate_docker_compose(_tech_doc(), "/tmp")
    complexities = [c.kwargs.get("complexity") for c in mock_llm.complete.await_args_list]
    assert "LOW" in complexities


def test_generate_env_example_non_empty() -> None:
    asm = PackageAssembler(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    text = asm._generate_env_example(_tech_doc())
    assert text.strip()
    assert "DATABASE_URL" in text


@pytest.mark.asyncio
async def test_validate_docker_build_called(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_qa: AsyncMock,
    tmp_path: Path,
) -> None:
    git = _git_mocks()
    with patch(
        "forgeai.delivery.package_assembler.ReadmeGenerator.generate",
        new=AsyncMock(return_value="# App\n\n## Setup\n1. a\n2. docker compose up\n3. b"),
    ):
        asm = PackageAssembler(db_session, git, mock_qa, mock_llm)
        await asm.assemble(str(uuid.uuid4()), _master(), _tech_doc(), str(tmp_path / "out2"))
    mock_qa.validate_docker_build.assert_awaited()


@pytest.mark.asyncio
async def test_files_written_non_empty(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_qa: AsyncMock,
    tmp_path: Path,
) -> None:
    git = _git_mocks()
    with patch(
        "forgeai.delivery.package_assembler.ReadmeGenerator.generate",
        new=AsyncMock(return_value="# App\n\n## Setup\n1. a\n2. docker compose up\n3. b"),
    ):
        asm = PackageAssembler(db_session, git, mock_qa, mock_llm)
        pkg = await asm.assemble(str(uuid.uuid4()), _master(), _tech_doc(), str(tmp_path / "out3"))
    assert len(pkg.files_written) > 0


@pytest.mark.asyncio
async def test_release_v1_tag_created(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    mock_qa: AsyncMock,
    tmp_path: Path,
) -> None:
    git = _git_mocks()
    with patch(
        "forgeai.delivery.package_assembler.ReadmeGenerator.generate",
        new=AsyncMock(return_value="# App\n\n## Setup\n1. a\n2. docker compose up\n3. b"),
    ):
        asm = PackageAssembler(db_session, git, mock_qa, mock_llm)
        pkg = await asm.assemble(str(uuid.uuid4()), _master(), _tech_doc(), str(tmp_path / "out4"))
    git.create_tag.assert_called_with("release-v1", "ForgeAI delivery release v1")
    assert pkg.release_tag == "release-v1"


@pytest.mark.asyncio
async def test_docker_build_passed_in_package(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
    tmp_path: Path,
) -> None:
    mock_qa = AsyncMock()
    mock_qa.validate_docker_build = AsyncMock(return_value=False)
    git = _git_mocks()
    lead = LeadAgent("lead_1", db_session)
    with patch(
        "forgeai.delivery.package_assembler.ReadmeGenerator.generate",
        new=AsyncMock(return_value="# App\n\n## Setup\n1. a\n2. docker compose up\n3. b"),
    ):
        asm = PackageAssembler(
            db_session, git, mock_qa, mock_llm, lead_agent=lead
        )
        pkg = await asm.assemble(str(uuid.uuid4()), _master(), _tech_doc(), str(tmp_path / "out5"))
    assert pkg.docker_build_passed is False
