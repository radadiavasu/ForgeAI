"""API route tests (Phase 10B) — mocked DB via dependency override."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.api.app import app
from forgeai.api.routes import get_db
from forgeai.lifecycle.project_registry import ProjectRegistry
from forgeai.models.task import Task, TaskComplexity
from forgeai.agents.lead_agent import LeadAgent
from forgeai.state_machine.states import TaskState


@pytest_asyncio.fixture
async def api_client(db_session: AsyncSession) -> AsyncClient:
    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_projects_returns_project_id(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/projects",
        json={"brief": "Build a todo app", "name": "todo-v1", "constraints": {}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"]
    uuid.UUID(data["project_id"])


@pytest.mark.asyncio
async def test_post_projects_returns_poll_url(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/projects",
        json={"brief": "Build a notes app", "name": "notes"},
    )
    data = resp.json()
    assert "poll_url" in data
    assert data["project_id"] in data["poll_url"]
    assert data["status"] == "bootstrapping"


@pytest.mark.asyncio
async def test_get_project_status_shape(
    db_session: AsyncSession,
    api_client: AsyncClient,
) -> None:
    registry = ProjectRegistry(db_session)
    project = await registry.create_project("Demo", "A brief")
    resp = await api_client.get(f"/projects/{project.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == project.id
    assert data["name"] == "Demo"
    assert "tasks_total" in data
    assert "pending_approvals" in data
    assert isinstance(data["pending_approvals"], list)


@pytest.mark.asyncio
async def test_get_project_unknown_returns_404(api_client: AsyncClient) -> None:
    resp = await api_client.get(f"/projects/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_approve_returns_200(
    db_session: AsyncSession,
    api_client: AsyncClient,
) -> None:
    registry = ProjectRegistry(db_session)
    project = await registry.create_project("Approve me", "Brief")
    resp = await api_client.post(
        f"/projects/{project.id}/approve",
        json={"approval_type": "tech_stack", "notes": "looks good"},
    )
    assert resp.status_code == 200
    assert resp.json()["approved"] is True


@pytest.mark.asyncio
async def test_post_changes_returns_change_response_shape(
    db_session: AsyncSession,
    api_client: AsyncClient,
) -> None:
    registry = ProjectRegistry(db_session)
    project = await registry.create_project("Live-ish", "Brief")
    resp = await api_client.post(
        f"/projects/{project.id}/changes",
        json={"change_request": "Fix the login button", "decision": "PROCEED"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == project.id
    assert data["change_type"]
    assert data["risk_level"]
    assert "estimated_cost_usd" in data
    assert "message" in data


@pytest.mark.asyncio
async def test_get_report_returns_report_shape(
    db_session: AsyncSession,
    api_client: AsyncClient,
) -> None:
    registry = ProjectRegistry(db_session)
    project = await registry.create_project("Report", "Brief for report")
    lead = LeadAgent("lead_1", db_session)
    task = await lead.create_task(
        "Done task",
        None,
        TaskComplexity.LOW,
        "backend_agent_1",
        project_id=uuid.UUID(project.id),
    )
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    from forgeai.agents.backend_agent import BackendAgent

    backend = BackendAgent("backend_agent_1", db_session)
    await backend.complete_work(task.id, output="x = 1")
    from forgeai.agents.qa_agent import QAAgent

    qa = QAAgent("qa_1", db_session)
    await qa.begin_review(task.id)
    await qa.approve(task.id, output="done")

    resp = await api_client.get(f"/projects/{project.id}/report")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks_completed"] >= 1
    assert "generated_at" in data
    assert "files_written" in data


@pytest.mark.asyncio
async def test_dashboard_returns_html(api_client: AsyncClient) -> None:
    resp = await api_client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_dashboard_html_contains_forgeai(api_client: AsyncClient) -> None:
    resp = await api_client.get("/dashboard")
    assert "ForgeAI" in resp.text


@pytest.mark.asyncio
async def test_dashboard_html_contains_projects(api_client: AsyncClient) -> None:
    resp = await api_client.get("/dashboard")
    assert "Projects" in resp.text
