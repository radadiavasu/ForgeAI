"""Agent bootstrap protocol (Phase 6)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from forgeai.agents.lead_agent import LeadAgent
from forgeai.bootstrap.protocol import AgentBootstrapProtocol
from forgeai.bootstrap.schemas import ApprovedConfig, TaskPlan
from forgeai.exceptions import BootstrapError
from forgeai.llm.schemas import (
    APISurface,
    Component,
    DataModel,
    MasterDocument,
    ResearchOutput,
    TechStack,
    TechStackDocument,
)
from forgeai.models.agent_lifecycle import AgentLifecycleEventModel


def _minimal_master() -> MasterDocument:
    stack = TechStack(
        language="Python",
        framework="FastAPI",
        database="PostgreSQL",
        testing_framework="pytest",
        rationale="test",
        rejected_alternatives=[],
    )
    return MasterDocument(
        project_name="TaskApp",
        project_summary="Tasks",
        components=[Component(name="UI", responsibility="x", dependencies=[], acceptance_criteria=[])],
        data_models=[DataModel(name="Task", fields=[])],
        api_surfaces=[APISurface(endpoint="/tasks", method="GET", description="list")],
        tech_stack=stack,
        constraints=[],
    )


def _minimal_research() -> ResearchOutput:
    stack = TechStack(
        language="Python",
        framework="FastAPI",
        database="PostgreSQL",
        testing_framework="pytest",
        rationale="r",
        rejected_alternatives=[],
    )
    return ResearchOutput(
        domain_summary="tasks",
        technology_options=[],
        recommended_stack=stack,
        constraints_respected=[],
        research_sources=[],
    )


@pytest.mark.asyncio
async def test_research_and_architect_created_without_human_approval(db_session):
    lead = LeadAgent("lead_1", db_session, llm_client=MagicMock(), agent_memory=MagicMock())
    proto = AgentBootstrapProtocol(lead)
    pid = uuid.uuid4()
    research = MagicMock()
    research.research = AsyncMock(return_value=_minimal_research())
    architect = MagicMock()
    architect.produce_master_document = AsyncMock(return_value=_minimal_master())
    architect.produce_tech_stack_document = AsyncMock(
        return_value=TechStackDocument(
            language="Python",
            framework="FastAPI",
            database="PostgreSQL",
            testing_framework="pytest",
            libraries=[],
            rationale="r",
            rejected_alternatives=[],
        )
    )

    async def _approve(rec):
        return ApprovedConfig(
            frontend_agent_count=1,
            backend_agent_count=1,
            qa_agent_count=1,
            approved_by="human",
        )

    with (
        patch.object(proto, "_create_research_agent", new=AsyncMock(return_value=research)),
        patch.object(proto, "_create_architect_agent", new=AsyncMock(return_value=architect)),
        patch.object(proto, "_create_execution_agents", new=AsyncMock(return_value=["frontend_agent_1"])),
        patch.object(lead, "decompose_tasks", new=AsyncMock(return_value=TaskPlan(total_tasks=1))),
        patch.object(lead, "write_to_project_memory", new=AsyncMock(return_value=uuid.uuid4())),
    ):
        await proto.run("brief", {}, _approve, project_id=pid)
    research.research.assert_awaited_once()
    architect.produce_master_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_human_callback_before_execution_agents(db_session):
    lead = LeadAgent("lead_1", db_session, llm_client=MagicMock(), agent_memory=MagicMock())
    proto = AgentBootstrapProtocol(lead)
    order: list[str] = []
    research = MagicMock()
    research.research = AsyncMock(return_value=_minimal_research())
    architect = MagicMock()
    architect.produce_master_document = AsyncMock(return_value=_minimal_master())
    architect.produce_tech_stack_document = AsyncMock(
        return_value=TechStackDocument(
            language="Python",
            framework="FastAPI",
            database="PostgreSQL",
            testing_framework="pytest",
            libraries=[],
            rationale="r",
            rejected_alternatives=[],
        )
    )

    async def _approve(rec):
        order.append("human")
        return ApprovedConfig(
            frontend_agent_count=1,
            backend_agent_count=0,
            qa_agent_count=1,
            approved_by="human",
        )

    async def _exec_side(*_a, **_k):
        order.append("exec")
        return ["a1"]

    with (
        patch.object(proto, "_create_research_agent", new=AsyncMock(return_value=research)),
        patch.object(proto, "_create_architect_agent", new=AsyncMock(return_value=architect)),
        patch.object(proto, "_create_execution_agents", new=AsyncMock(side_effect=_exec_side)),
        patch.object(lead, "decompose_tasks", new=AsyncMock(return_value=TaskPlan(total_tasks=1))),
        patch.object(lead, "write_to_project_memory", new=AsyncMock(return_value=uuid.uuid4())),
    ):
        await proto.run("brief", {}, _approve, project_id=uuid.uuid4())
    assert order.index("human") < order.index("exec")


@pytest.mark.asyncio
async def test_bootstrap_result_fields(db_session):
    lead = LeadAgent("lead_1", db_session, llm_client=MagicMock(), agent_memory=MagicMock())
    proto = AgentBootstrapProtocol(lead)
    md = _minimal_master()
    research = MagicMock()
    research.research = AsyncMock(return_value=_minimal_research())
    architect = MagicMock()
    architect.produce_master_document = AsyncMock(return_value=md)
    architect.produce_tech_stack_document = AsyncMock(
        return_value=TechStackDocument(
            language="Python",
            framework="FastAPI",
            database="PostgreSQL",
            testing_framework="pytest",
            libraries=[],
            rationale="r",
            rejected_alternatives=[],
        )
    )

    async def _approve(rec):
        return ApprovedConfig(
            frontend_agent_count=1,
            backend_agent_count=1,
            qa_agent_count=1,
            approved_by="human",
        )

    with (
        patch.object(proto, "_create_research_agent", new=AsyncMock(return_value=research)),
        patch.object(proto, "_create_architect_agent", new=AsyncMock(return_value=architect)),
        patch.object(proto, "_create_execution_agents", new=AsyncMock(return_value=["a1", "b1"])),
        patch.object(lead, "decompose_tasks", new=AsyncMock(return_value=TaskPlan(total_tasks=2))),
        patch.object(lead, "write_to_project_memory", new=AsyncMock(return_value=uuid.uuid4())),
    ):
        out = await proto.run("brief", {}, _approve, project_id=uuid.uuid4())
    assert out.master_document.project_name == "TaskApp"
    assert out.tech_stack_document.language == "Python"
    assert out.task_plan.total_tasks == 2
    assert out.agents_created == ["a1", "b1"]
    assert out.recommendation.frontend_agent_count >= 1


@pytest.mark.asyncio
async def test_agent_creation_logged(db_session):
    lead = LeadAgent("lead_1", db_session, llm_client=MagicMock(), agent_memory=MagicMock())
    await lead.log_agent_lifecycle_event(
        agent_id="x1",
        agent_role="research_agent",
        event_type="created",
        created_by="lead_agent",
        project_id=uuid.uuid4(),
    )
    r = await db_session.execute(select(AgentLifecycleEventModel).where(AgentLifecycleEventModel.agent_id == "x1"))
    row = r.scalar_one()
    assert row.event_type == "created"


@pytest.mark.asyncio
async def test_bootstrap_master_document_failure(db_session):
    lead = LeadAgent("lead_1", db_session, llm_client=MagicMock(), agent_memory=MagicMock())
    proto = AgentBootstrapProtocol(lead)
    research = MagicMock()
    research.research = AsyncMock(return_value=_minimal_research())
    architect = MagicMock()
    architect.produce_master_document = AsyncMock(side_effect=ValueError("bad json"))
    architect.produce_tech_stack_document = AsyncMock()

    async def _approve(rec):
        return ApprovedConfig(
            frontend_agent_count=1,
            backend_agent_count=0,
            qa_agent_count=1,
            approved_by="human",
        )

    with (
        patch.object(proto, "_create_research_agent", new=AsyncMock(return_value=research)),
        patch.object(proto, "_create_architect_agent", new=AsyncMock(return_value=architect)),
        patch.object(lead, "write_to_project_memory", new=AsyncMock(return_value=uuid.uuid4())),
    ):
        with pytest.raises(BootstrapError, match="Master_Document"):
            await proto.run("brief", {}, _approve, project_id=uuid.uuid4())
