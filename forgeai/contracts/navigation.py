"""Navigation contract negotiation (Req 27, Phase 6)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from forgeai.contracts.schemas import LayoutSpecification, NavigationContract, RouteDefinition
from forgeai.llm.client import LLMClient

if TYPE_CHECKING:
    from forgeai.agents.frontend_agent import FrontendAgent
    from forgeai.agents.lead_agent import LeadAgent

logger = logging.getLogger(__name__)


class NavigationNegotiator:
    """Mediates route ownership between Frontend_Agents."""

    def __init__(self, lead_agent: LeadAgent, llm_client: LLMClient) -> None:
        self.lead = lead_agent
        self.llm = llm_client

    def _validate_contract(self, contract: NavigationContract) -> None:
        paths = [r.path for r in contract.routes]
        if len(paths) != len(set(paths)):
            raise ValueError("NavigationContract has duplicate paths")
        roots = [r for r in contract.routes if r.is_root_layout]
        if len(roots) != 1:
            raise ValueError("NavigationContract must have exactly one is_root_layout route")
        root = roots[0]
        if root.owner_agent_id != contract.shared_layout_owner:
            raise ValueError("Root layout owner must match shared_layout_owner")

    async def negotiate(
        self,
        frontend_agents: list[FrontendAgent],
        layout_spec: LayoutSpecification,
        project_id: str,
    ) -> NavigationContract:
        proposals: dict[str, list[RouteDefinition]] = {}
        for agent in frontend_agents:
            proposals[agent.agent_id] = await self._get_agent_proposal(agent, layout_spec)
        resolved = self._resolve_conflicts(proposals)
        first_id = frontend_agents[0].agent_id if frontend_agents else ""

        flat: list[RouteDefinition] = []
        seen_paths: set[str] = set()
        for aid in [a.agent_id for a in frontend_agents]:
            for r in resolved.get(aid, []):
                if r.path in seen_paths:
                    continue
                seen_paths.add(r.path)
                flat.append(
                    RouteDefinition(
                        path=r.path,
                        owner_agent_id=aid,
                        component_name=r.component_name,
                        is_root_layout=False,
                    )
                )
        if flat:
            root_idx = next(
                (i for i, r in enumerate(flat) if r.owner_agent_id == first_id),
                0,
            )
            r = flat[root_idx]
            flat[root_idx] = RouteDefinition(
                path=r.path,
                owner_agent_id=r.owner_agent_id,
                component_name=r.component_name,
                is_root_layout=True,
            )

        shared_name = (
            layout_spec.shared_components[0].name
            if layout_spec.shared_components
            else "AppLayout"
        )
        contract = NavigationContract(
            project_id=project_id,
            routes=flat,
            shared_layout_component=shared_name,
            shared_layout_owner=first_id,
            linking_convention="react-router-dom Link component",
            created_at=datetime.now(UTC),
            approved_by=self.lead.agent_id,
        )
        self._validate_contract(contract)
        await self.lead.write_to_project_memory(
            "navigation_contract",
            contract.model_dump(mode="json"),
            project_id=uuid.UUID(str(project_id)),
        )
        logger.info("[NAV] Navigation contract persisted for project %s", project_id)
        return contract

    async def _get_agent_proposal(
        self,
        agent: FrontendAgent,
        layout_spec: LayoutSpecification,
    ) -> list[RouteDefinition]:
        return await agent.propose_routes(layout_spec)

    def _resolve_conflicts(
        self,
        proposals: dict[str, list[RouteDefinition]],
    ) -> dict[str, list[RouteDefinition]]:
        path_to_agents: dict[str, list[str]] = {}
        for aid, routes in proposals.items():
            for r in routes:
                path_to_agents.setdefault(r.path, []).append(aid)
        out: dict[str, list[RouteDefinition]] = {k: list(v) for k, v in proposals.items()}
        for path, agents in path_to_agents.items():
            if len(agents) <= 1:
                continue
            winner = min(agents, key=lambda a: len(out.get(a, [])))
            for a in agents:
                if a != winner:
                    out[a] = [x for x in out[a] if x.path != path]
        return out

    async def mediate_proposals_llm(
        self,
        *,
        layout_spec_json: str,
        proposals_summary: str,
    ) -> str:
        """Optional LLM assist for conflict narrative (defensive JSON path elsewhere)."""
        resp = await self.llm.complete(
            system_prompt="You mediate frontend route ownership. Output one JSON object only: "
            '{"summary": string}',
            user_message=f"Layout:\n{layout_spec_json}\n\nProposals:\n{proposals_summary}",
            complexity="LOW",
            loop_count=0,
            max_tokens=512,
        )
        return resp.content.strip()
