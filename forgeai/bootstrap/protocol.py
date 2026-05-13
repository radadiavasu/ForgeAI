"""Mandatory agent bootstrap sequence (Req 29, Phase 6)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from forgeai.bootstrap.schemas import (
    AgentRecommendation,
    ApprovedConfig,
    BootstrapResult,
    TaskPlan,
    TaskSpec,
)
from forgeai.exceptions import BootstrapError
from forgeai.llm.schemas import MasterDocument, TechStackDocument

if TYPE_CHECKING:
    from forgeai.agents.architect_agent import ArchitectAgent
    from forgeai.agents.lead_agent import LeadAgent
    from forgeai.agents.research_agent import ResearchAgent

logger = logging.getLogger(__name__)


class AgentBootstrapProtocol:
    """Orchestrates research → architecture → human approval → execution agents."""

    def __init__(self, lead_agent: LeadAgent) -> None:
        self.lead = lead_agent
        self._project_id: uuid.UUID | None = None

    async def run(
        self,
        project_brief: str,
        preflight_constraints: dict,
        human_approval_callback: Callable[[AgentRecommendation], Awaitable[ApprovedConfig]],
        *,
        project_id: uuid.UUID | None = None,
    ) -> BootstrapResult:
        self._project_id = project_id or uuid.uuid4()
        pid = self._project_id

        print("[BOOTSTRAP] Step 2: Creating Research_Agent + Architect_Agent...")
        research_agent = await self._create_research_agent()
        architect_agent = await self._create_architect_agent()

        print("[BOOTSTRAP] Step 3: Running Research & Architecture phase...")
        try:
            research_output = await research_agent.research(project_brief, preflight_constraints)
        except Exception as exc:
            raise BootstrapError("Research phase failed") from exc

        try:
            master_doc = await architect_agent.produce_master_document(
                project_brief, research_output, preflight_constraints
            )
            tech_stack = await architect_agent.produce_tech_stack_document(research_output)
        except Exception as exc:
            raise BootstrapError("Master_Document production failed") from exc

        print("[RESEARCH] Research complete")
        print(
            f"[ARCHITECT] Master_Document complete — {len(master_doc.components)} components, "
            f"{len(master_doc.api_surfaces)} APIs"
        )

        await self.lead.write_to_project_memory(
            "master_document",
            master_doc,
            project_id=pid,
        )
        await self.lead.write_to_project_memory(
            "tech_stack_document",
            tech_stack,
            project_id=pid,
        )

        print("[BOOTSTRAP] Step 4: Analysing task decomposition...")
        task_plan = await self.lead.decompose_tasks(master_doc)
        recommendation = self._build_recommendation(task_plan)

        print("[BOOTSTRAP] Step 5: Agent recommendation:")
        print(f"  {recommendation.reasoning.strip()}")

        print("[BOOTSTRAP] Step 6: Human approved configuration")
        approved_config = await human_approval_callback(recommendation)

        print("[BOOTSTRAP] Step 7: Creating execution agents...")
        execution_ids = await self._create_execution_agents(approved_config, master_doc)

        return BootstrapResult(
            master_document=master_doc,
            tech_stack_document=tech_stack,
            task_plan=task_plan,
            agents_created=execution_ids,
            recommendation=recommendation,
        )

    async def _create_research_agent(self) -> ResearchAgent:
        agent_id = "research_agent_1"
        await self.lead.log_agent_lifecycle_event(
            agent_id=agent_id,
            agent_role="research_agent",
            event_type="created",
            created_by="lead_agent",
            project_id=self._project_id,
            development_phase="bootstrap",
        )
        print(f"[BOOTSTRAP] {agent_id} created")
        return self.lead.build_research_agent(agent_id)

    async def _create_architect_agent(self) -> ArchitectAgent:
        from forgeai.agents.architect_agent import ArchitectAgent

        agent_id = "architect_agent_1"
        await self.lead.log_agent_lifecycle_event(
            agent_id=agent_id,
            agent_role="architect_agent",
            event_type="created",
            created_by="lead_agent",
            project_id=self._project_id,
            development_phase="bootstrap",
        )
        print(f"[BOOTSTRAP] {agent_id} created")
        return self.lead.build_architect_agent(agent_id)

    async def _create_execution_agents(
        self,
        config: ApprovedConfig,
        master_doc: MasterDocument,
    ) -> list[str]:
        created: list[str] = []
        for i in range(config.frontend_agent_count):
            aid = f"frontend_agent_{i + 1}"
            await self.lead.log_agent_lifecycle_event(
                agent_id=aid,
                agent_role="frontend_agent",
                event_type="created",
                created_by="lead_agent",
                project_id=self._project_id,
                development_phase="execution",
            )
            print(f"[BOOTSTRAP] {aid} created")
            created.append(aid)
        for i in range(config.backend_agent_count):
            aid = f"backend_agent_{i + 1}"
            await self.lead.log_agent_lifecycle_event(
                agent_id=aid,
                agent_role="backend_agent",
                event_type="created",
                created_by="lead_agent",
                project_id=self._project_id,
                development_phase="execution",
            )
            print(f"[BOOTSTRAP] {aid} created")
            created.append(aid)
        for i in range(config.qa_agent_count):
            aid = f"qa_agent_{i + 1}"
            await self.lead.log_agent_lifecycle_event(
                agent_id=aid,
                agent_role="qa_agent",
                event_type="created",
                created_by="lead_agent",
                project_id=self._project_id,
                development_phase="execution",
            )
            print(f"[BOOTSTRAP] {aid} created")
            created.append(aid)
        _ = master_doc
        return created

    def _build_recommendation(self, task_plan: TaskPlan) -> AgentRecommendation:
        n_fe_tasks = len(task_plan.frontend_tasks)
        n_be_tasks = len(task_plan.backend_tasks)
        fe_agents = 2 if n_fe_tasks >= 3 else max(1, min(2, n_fe_tasks))
        be_agents = 1 if n_be_tasks else 1
        qa_agents = 1
        minutes = 25 + 4 * task_plan.total_tasks
        cost = round(0.08 + 0.012 * max(task_plan.total_tasks, 1), 2)
        reasoning = (
            f"Based on the project, I recommend:\n"
            f"  - {fe_agents} Frontend Agents ({n_fe_tasks} frontend tasks, parallelisable after root layout)\n"
            f"  - {be_agents} Backend Agent ({n_be_tasks} backend tasks)\n"
            f"  - {qa_agents} QA Agent (shared across phases)\n"
            f"  Estimated time with this config: ~{minutes} minutes\n"
            f"  Cost estimate: ~${cost:.2f}"
        )
        return AgentRecommendation(
            frontend_agent_count=fe_agents,
            backend_agent_count=be_agents,
            qa_agent_count=qa_agents,
            reasoning=reasoning,
            time_estimate_minutes=minutes,
            cost_estimate_usd=cost,
        )

    @staticmethod
    def default_task_plan() -> TaskPlan:
        """Deterministic task plan for the Phase 6 personal task manager brief."""
        root = "Build AppLayout — shared shell, NavBar, Footer"
        return TaskPlan(
            frontend_tasks=[
                TaskSpec(
                    title=root,
                    description="Root layout and shared navigation shell.",
                    complexity="MEDIUM",
                    phase="FRONTEND_PHASE",
                    dependencies=[],
                ),
                TaskSpec(
                    title="Build Dashboard page",
                    description="Task list, add task form, completion toggle.",
                    complexity="LOW",
                    phase="FRONTEND_PHASE",
                    dependencies=[root],
                ),
                TaskSpec(
                    title="Build History page",
                    description="Completed tasks with timestamps.",
                    complexity="LOW",
                    phase="FRONTEND_PHASE",
                    dependencies=[root],
                ),
                TaskSpec(
                    title="Build Settings page",
                    description="Theme and notification preferences.",
                    complexity="LOW",
                    phase="FRONTEND_PHASE",
                    dependencies=[root],
                ),
            ],
            backend_tasks=[
                TaskSpec(
                    title="REST API for tasks",
                    description="CRUD and history endpoints.",
                    complexity="MEDIUM",
                    phase="BACKEND_PHASE",
                    dependencies=[],
                ),
            ],
            total_tasks=5,
            estimated_complexity_distribution={"LOW": 3, "MEDIUM": 2, "HIGH": 0},
        )
