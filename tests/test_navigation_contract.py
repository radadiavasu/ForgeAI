"""Navigation contract negotiation (Phase 6)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from forgeai.agents.frontend_agent import FrontendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.contracts.navigation import NavigationNegotiator
from forgeai.contracts.schemas import (
    LayoutSpecification,
    NavigationContract,
    PageSpec,
    RouteDefinition,
    SharedComponentSpec,
)
from forgeai.llm.client import LLMClient


@pytest.mark.asyncio
async def test_each_agent_proposes_at_least_one_route(db_session):
    lead = LeadAgent("lead_1", db_session, llm_client=MagicMock(spec=LLMClient), agent_memory=MagicMock())
    layout = LayoutSpecification(
        project_id="p1",
        source="architect_generated",
        pages=[
            PageSpec(name="A", route="/a", sections=[], interactions=[], acceptance_criteria=[]),
            PageSpec(name="B", route="/b", sections=[], interactions=[], acceptance_criteria=[]),
        ],
        shared_components=[SharedComponentSpec(name="AppLayout", used_by_pages=[], props=[], description="")],
        design_tokens={},
    )
    fe1 = FrontendAgent("frontend_agent_1", db_session, None, None, None, None)
    fe2 = FrontendAgent("frontend_agent_2", db_session, None, None, None, None)
    p1 = await fe1.propose_routes(layout)
    p2 = await fe2.propose_routes(layout)
    assert len(p1) >= 1
    assert len(p2) >= 1


@pytest.mark.asyncio
async def test_conflict_resolution_two_agents_same_route(db_session):
    lead = LeadAgent("lead_1", db_session, llm_client=MagicMock(spec=LLMClient), agent_memory=MagicMock())
    neg = NavigationNegotiator(lead, MagicMock(spec=LLMClient))
    proposals = {
        "a": [
            RouteDefinition(path="/x", owner_agent_id="a", component_name="A1", is_root_layout=False),
            RouteDefinition(path="/y", owner_agent_id="a", component_name="A2", is_root_layout=False),
        ],
        "b": [RouteDefinition(path="/x", owner_agent_id="b", component_name="B1", is_root_layout=False)],
    }
    resolved = neg._resolve_conflicts(proposals)
    owners_for_x = [r.owner_agent_id for aid, routes in resolved.items() for r in routes if r.path == "/x"]
    assert owners_for_x.count("a") + owners_for_x.count("b") == 1


@pytest.mark.asyncio
async def test_one_root_layout_and_persisted(db_session):
    lead = LeadAgent("lead_1", db_session, llm_client=MagicMock(spec=LLMClient), agent_memory=MagicMock())
    lead.write_to_project_memory = AsyncMock(return_value=uuid.uuid4())
    layout = LayoutSpecification(
        project_id="p1",
        source="architect_generated",
        pages=[
            PageSpec(name="Dashboard", route="/", sections=[], interactions=[], acceptance_criteria=[]),
            PageSpec(name="History", route="/history", sections=[], interactions=[], acceptance_criteria=[]),
            PageSpec(name="Settings", route="/settings", sections=[], interactions=[], acceptance_criteria=[]),
        ],
        shared_components=[SharedComponentSpec(name="AppLayout", used_by_pages=[], props=[], description="")],
        design_tokens={},
    )
    fe1 = FrontendAgent("frontend_agent_1", db_session, None, None, None, None)
    fe2 = FrontendAgent("frontend_agent_2", db_session, None, None, None, None)
    neg = NavigationNegotiator(lead, MagicMock(spec=LLMClient))
    pid = str(uuid.uuid4())
    contract = await neg.negotiate([fe1, fe2], layout, pid)
    assert sum(1 for r in contract.routes if r.is_root_layout) == 1
    assert contract.shared_layout_component == "AppLayout"
    assert contract.approved_by == "lead_1"
    lead.write_to_project_memory.assert_awaited()


@pytest.mark.asyncio
async def test_navigation_contract_schema_defaults(db_session):
    c = NavigationContract(
        project_id="p",
        routes=[
            RouteDefinition(path="/", owner_agent_id="fe1", component_name="Root", is_root_layout=True),
        ],
        shared_layout_owner="fe1",
    )
    assert c.shared_layout_component == "AppLayout"
    assert c.approved_by == "lead_agent"
