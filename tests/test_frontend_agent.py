"""Frontend agent (Phase 6)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from forgeai.agents.frontend_agent import FrontendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.contracts.registry import ComponentRegistry
from forgeai.contracts.schemas import (
    LayoutSpecification,
    NavigationContract,
    PageSpec,
    RouteDefinition,
    SharedComponentSpec,
)
from forgeai.llm.client import LLMClient
from forgeai.memory.agent_memory import AgentMemory
from forgeai.models.task import TaskComplexity
from forgeai.state_machine.states import TaskState


@pytest.fixture
def llm_response():
    return MagicMock(
        content=(
            '{"code": "const X = () => <div>Hi</div>", '
            '"test_code": "def test_x(): assert isinstance(GENERATED_UI, str)", '
            '"components_registered": ["TaskCard"], '
            '"components_imported": ["NavBar"], '
            '"file_path": "src/pages/Dashboard.jsx"}'
        )
    )


@pytest.mark.asyncio
async def test_complete_work_queries_registry_and_memory(db_session, llm_response):
    llm = MagicMock(spec=LLMClient)
    llm.complete = AsyncMock(return_value=llm_response)
    memory = MagicMock(spec=AgentMemory)
    memory.retrieve_lessons = AsyncMock(return_value=[])
    pid = str(uuid.uuid4())
    reg = ComponentRegistry(db_session)
    await reg.register(pid, "NavBar", "fe0", "nav props", "src/NavBar.jsx")
    nav = NavigationContract(
        project_id=pid,
        routes=[
            RouteDefinition(path="/", owner_agent_id="fe1", component_name="Dash", is_root_layout=True),
        ],
        shared_layout_owner="fe1",
    )
    fe = FrontendAgent("frontend_agent_1", db_session, llm, memory, reg, nav)
    lead = LeadAgent("lead_fixture", db_session)
    task = await lead.create_task(
        "t",
        None,
        TaskComplexity.LOW,
        "frontend_agent_1",
        project_id=uuid.UUID(pid),
    )
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    page = PageSpec(name="Dashboard", route="/", sections=[], interactions=[], acceptance_criteria=[])
    await fe.complete_work(task.id, "Build dashboard", page, loop_count=0)
    memory.retrieve_lessons.assert_awaited()
    llm.complete.assert_awaited()
    call_kw = llm.complete.await_args.kwargs
    assert call_kw.get("complexity") == "LOW"


@pytest.mark.asyncio
async def test_frontend_output_fields(db_session, llm_response):
    llm = MagicMock(spec=LLMClient)
    llm.complete = AsyncMock(return_value=llm_response)
    memory = MagicMock(spec=AgentMemory)
    memory.retrieve_lessons = AsyncMock(return_value=[])
    pid = str(uuid.uuid4())
    reg = ComponentRegistry(db_session)
    nav = NavigationContract(
        project_id=pid,
        routes=[
            RouteDefinition(path="/", owner_agent_id="fe1", component_name="Dash", is_root_layout=True),
        ],
        shared_layout_owner="fe1",
    )
    fe = FrontendAgent("frontend_agent_1", db_session, llm, memory, reg, nav)
    lead = LeadAgent("lead_fixture", db_session)
    task = await lead.create_task(
        "t2",
        None,
        TaskComplexity.LOW,
        "frontend_agent_1",
        project_id=uuid.UUID(pid),
    )
    await lead.approve_phase_transition(task.id)
    await lead.assign_task(task.id)
    page = PageSpec(name="Dashboard", route="/", sections=[], interactions=[], acceptance_criteria=[])
    updated = await fe.complete_work(task.id, "d", page)
    assert updated.current_state == TaskState.IN_REVIEW


@pytest.mark.asyncio
async def test_propose_routes_llm_returns_definitions(db_session):
    llm = MagicMock(spec=LLMClient)
    llm.complete = AsyncMock(
        return_value=MagicMock(
            content='[{"path": "/z", "component_name": "ZPage", "is_root_layout": true}]'
        )
    )
    memory = MagicMock(spec=AgentMemory)
    layout = LayoutSpecification(
        project_id="p",
        source="x",
        pages=[PageSpec(name="Z", route="/z", sections=[], interactions=[], acceptance_criteria=[])],
        shared_components=[],
        design_tokens={},
    )
    fe = FrontendAgent("frontend_agent_9", db_session, llm, memory, None, None)
    routes = await fe.propose_routes(layout)
    assert len(routes) >= 1
    assert isinstance(routes[0], RouteDefinition)
