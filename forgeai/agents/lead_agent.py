"""Lead agent: task creation, lifecycle transitions, project artefacts."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy import func as sa_func, select, update

from forgeai.agents.architect_agent import ArchitectAgent
from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.base import BaseAgent
from forgeai.agents.frontend_agent import FrontendAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.agents.research_agent import ResearchAgent
from forgeai.bootstrap.protocol import AgentBootstrapProtocol
from forgeai.bootstrap.schemas import AgentRecommendation, ApprovedConfig, BootstrapResult, TaskPlan
from forgeai.contracts.navigation import NavigationNegotiator
from forgeai.contracts.registry import ComponentRegistry
from forgeai.contracts.schemas import LayoutSpecification, NavigationContract, PageSpec
from forgeai.escalation.ladder import EscalationLadder
from forgeai.escalation.loop_counter import LoopCounter
from forgeai.escalation.persistence import EscalationPersistence
from forgeai.orchestration.phase_gate import PhaseGate
from forgeai.orchestration.qa_loop import QAOrchestrator
from forgeai.orchestration.schemas import (
    DefectReport,
    FrontendPhaseResult,
    PhaseGateResult,
    QADecision,
)
from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import MasterDocument, TechStackDocument
from forgeai.memory.agent_memory import AgentMemory
from forgeai.memory.task_memory import TaskMemory
from forgeai.models.agent_lifecycle import AgentLifecycleEventModel
from forgeai.models.project_artefact import ProjectArtefactModel
from forgeai.models.task import Task, TaskComplexity
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_METADATA, KEY_PHASE_APPROVAL, KEY_WORK_OUTPUT

logger = logging.getLogger(__name__)


def _strip_json_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s.strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    s = _strip_json_fence(text)
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else {"items": out}
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            out = json.loads(m.group(0))
            return out if isinstance(out, dict) else {"items": out}
        raise


class LeadAgent(BaseAgent):
    """Creates tasks and performs lead-side transitions."""

    def __init__(
        self,
        agent_id: str,
        db_session,
        *,
        task_memory: TaskMemory | None = None,
        llm_client: LLMClient | None = None,
        agent_memory: AgentMemory | None = None,
    ) -> None:
        super().__init__(agent_id, db_session, task_memory=task_memory)
        self._llm_client = llm_client
        self._agent_memory = agent_memory

    async def create_task(
        self,
        title: str,
        description: str | None,
        complexity: TaskComplexity,
        assigned_agent: str,
        project_id: uuid.UUID | None = None,
        *,
        dependency_titles: list[str] | None = None,
    ) -> Task:
        """Insert a new task in ``PHASE_LOCKED`` state.

        Args:
            title: Human-readable title.
            description: Optional longer description.
            complexity: LOW/MEDIUM/HIGH.
            assigned_agent: Agent id string assigned to execute the task.
            project_id: Optional project scope; random UUID if omitted.

        Returns:
            The persisted ``Task``.

        Raises:
            sqlalchemy.exc.SQLAlchemyError: On persistence failures.
        """
        pid = project_id or uuid.uuid4()
        task = Task(
            project_id=pid,
            title=title,
            description=description,
            assigned_agent=assigned_agent,
            complexity=complexity,
            current_state=TaskState.PHASE_LOCKED,
            dependency_titles=dependency_titles,
        )
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def approve_phase_transition(self, task_id: uuid.UUID) -> Task:
        """Move ``PHASE_LOCKED`` → ``TODO`` with approval.

        Args:
            task_id: Target task id.

        Returns:
            Updated task after transition.

        Raises:
            forgeai.exceptions.InvalidTransitionError: If the edge is invalid.
            forgeai.exceptions.TransitionConditionError: If approval is missing.
        """
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        return await machine.transition(
            task_id,
            TaskState.TODO,
            self.agent_id,
            **{KEY_PHASE_APPROVAL: True},
        )

    async def assign_task(self, task_id: uuid.UUID) -> Task:
        """Move ``TODO`` → ``IN_PROGRESS``.

        Args:
            task_id: Target task id.

        Returns:
            Updated task after transition.

        Raises:
            forgeai.exceptions.InvalidTransitionError: If the edge is invalid.
            forgeai.exceptions.TransitionConditionError: On condition failures.
        """
        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        return await machine.transition(
            task_id,
            TaskState.IN_PROGRESS,
            self.agent_id,
        )

    async def persist_versioned_artefact(
        self,
        project_id: uuid.UUID,
        artefact_type: str,
        content: dict,
        created_by: str,
    ) -> uuid.UUID:
        """Insert a new artefact version; previous same-type rows marked not current."""
        max_ver = (
            await self.db.execute(
                select(sa_func.max(ProjectArtefactModel.version)).where(
                    ProjectArtefactModel.project_id == project_id,
                    ProjectArtefactModel.artefact_type == artefact_type,
                )
            )
        ).scalar_one_or_none()
        next_version = int(max_ver or 0) + 1

        await self.db.execute(
            update(ProjectArtefactModel)
            .where(
                ProjectArtefactModel.project_id == project_id,
                ProjectArtefactModel.artefact_type == artefact_type,
                ProjectArtefactModel.is_current.is_(True),
            )
            .values(is_current=False)
        )

        row = ProjectArtefactModel(
            project_id=project_id,
            artefact_type=artefact_type,
            content=content,
            version=next_version,
            is_current=True,
            created_by=created_by,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row.id

    async def persist_master_and_tech_stack_documents(
        self,
        project_id: uuid.UUID,
        master_document: MasterDocument,
        tech_stack_document: TechStackDocument,
        created_by: str,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Save Master_Document and Tech_Stack_Document as versioned JSONB rows."""
        mid = await self.persist_versioned_artefact(
            project_id,
            "master_document",
            master_document.model_dump(mode="json"),
            created_by,
        )
        tid = await self.persist_versioned_artefact(
            project_id,
            "tech_stack_document",
            tech_stack_document.model_dump(mode="json"),
            created_by,
        )
        return mid, tid

    async def write_to_project_memory(
        self,
        key: str,
        value: MasterDocument | TechStackDocument | dict[str, Any] | PydanticBaseModel,
        *,
        project_id: uuid.UUID,
    ) -> uuid.UUID:
        """Persist a versioned JSON artefact (Project_Memory)."""
        if isinstance(value, dict):
            content = value
        elif hasattr(value, "model_dump"):
            content = value.model_dump(mode="json")  # type: ignore[union-attr]
        else:
            content = {"text": str(value)}
        return await self.persist_versioned_artefact(
            project_id, key, content, created_by=self.agent_id
        )

    async def log_agent_lifecycle_event(
        self,
        *,
        agent_id: str,
        agent_role: str,
        event_type: str,
        created_by: str,
        project_id: uuid.UUID | None = None,
        development_phase: str | None = None,
    ) -> uuid.UUID:
        row = AgentLifecycleEventModel(
            agent_id=agent_id,
            agent_role=agent_role,
            event_type=event_type,
            created_by=created_by,
            project_id=project_id,
            development_phase=development_phase,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row.id

    def build_research_agent(self, agent_id: str) -> ResearchAgent:
        if self._llm_client is None or self._agent_memory is None:
            raise RuntimeError("LeadAgent needs llm_client and agent_memory to build ResearchAgent")
        return ResearchAgent(agent_id, self.db, self._llm_client, self._agent_memory)

    def build_architect_agent(self, agent_id: str) -> ArchitectAgent:
        if self._llm_client is None or self._agent_memory is None:
            raise RuntimeError("LeadAgent needs llm_client and agent_memory to build ArchitectAgent")
        return ArchitectAgent(agent_id, self.db, self._llm_client, self._agent_memory)

    async def run_bootstrap(
        self,
        project_brief: str,
        preflight_constraints: dict,
        human_approval_callback: Callable[[AgentRecommendation], Awaitable[ApprovedConfig]],
        *,
        project_id: uuid.UUID | None = None,
    ) -> BootstrapResult:
        proto = AgentBootstrapProtocol(self)
        return await proto.run(
            project_brief,
            preflight_constraints,
            human_approval_callback,
            project_id=project_id,
        )

    async def decompose_tasks(self, master_doc: MasterDocument) -> TaskPlan:
        if self._llm_client is None:
            return AgentBootstrapProtocol.default_task_plan()
        user_message = (
            "Decompose the following Master_Document into a JSON object with keys: "
            "frontend_tasks, backend_tasks, total_tasks, estimated_complexity_distribution.\n"
            "Each task must have: title, description, complexity (LOW|MEDIUM|HIGH), "
            "phase (FRONTEND_PHASE|BACKEND_PHASE), dependencies (array of task titles).\n"
            "Include a root frontend task titled exactly "
            "'Build AppLayout — shared shell, NavBar, Footer' with no dependencies. "
            "All other FRONTEND_PHASE tasks must list that title in dependencies.\n\n"
            f"{master_doc.model_dump_json()}"
        )
        resp = await self._llm_client.complete(
            system_prompt="You are Lead_Agent. Output JSON only.",
            user_message=user_message,
            complexity="HIGH",
            loop_count=0,
            max_tokens=8192,
        )
        try:
            raw = _extract_json_object(resp.content)
            return TaskPlan.model_validate(raw)
        except Exception:
            logger.warning("[LEAD] TaskPlan parse failed; using default task plan")
            return AgentBootstrapProtocol.default_task_plan()

    async def initiate_navigation_contract(
        self,
        frontend_agents: list[FrontendAgent],
        layout_spec: LayoutSpecification,
        project_id: str,
    ) -> NavigationContract:
        if self._llm_client is None:
            raise RuntimeError("LeadAgent needs llm_client for navigation negotiation")
        neg = NavigationNegotiator(self, self._llm_client)
        return await neg.negotiate(frontend_agents, layout_spec, project_id)

    async def review_layout_spec(
        self,
        layout_spec: LayoutSpecification,
        project_brief: str,
    ) -> tuple[bool, str]:
        if self._llm_client is None:
            return True, ""
        user_message = (
            f"Project brief:\n{project_brief}\n\n"
            f"Layout specification JSON:\n{layout_spec.model_dump_json()}\n\n"
            "Respond with JSON only: {\"approved\": boolean, \"feedback\": string}. "
            "Approve only if every page has clear acceptance_criteria and shared components fit."
        )
        resp = await self._llm_client.complete(
            system_prompt="You are Lead_Agent reviewing a UI layout specification.",
            user_message=user_message,
            complexity="MEDIUM",
            loop_count=0,
            max_tokens=2048,
        )
        try:
            data = _extract_json_object(resp.content)
            approved = bool(data.get("approved", True))
            feedback = str(data.get("feedback", "")).strip()
            return approved, feedback
        except Exception:
            return True, ""

    async def unlock_dependent_tasks(
        self,
        completed_task_title: str,
        project_id: uuid.UUID,
    ) -> list[str]:
        res = await self.db.execute(
            select(Task).where(
                Task.project_id == project_id,
                Task.current_state == TaskState.PHASE_LOCKED,
            )
        )
        unlocked: list[str] = []
        for task in res.scalars():
            deps = task.dependency_titles or []
            if completed_task_title in deps:
                await self.approve_phase_transition(task.id)
                unlocked.append(task.title)
        return unlocked

    async def generate_layout_spec(
        self,
        master_doc: MasterDocument,
        project_id: str,
    ) -> LayoutSpecification:
        """Path B — delegate to ``Architect_Agent`` (Req 22)."""
        arch = self.build_architect_agent("architect_agent_1")
        return await arch.generate_layout_spec(master_doc, project_id)

    async def process_mockup(self, mockup_file_path: str, project_id: str) -> LayoutSpecification:
        """Path A — read mockup file and delegate to ``Architect_Agent`` (Req 22)."""
        from pathlib import Path

        _ = Path(mockup_file_path).read_bytes()
        arch = self.build_architect_agent("architect_agent_1")
        return await arch.process_mockup_layout(mockup_file_path, project_id)

    def build_qa_orchestrator(
        self,
        loop_counter: LoopCounter,
        escalation_ladder: EscalationLadder,
    ) -> QAOrchestrator:
        if self._llm_client is None:
            raise RuntimeError("LeadAgent needs llm_client for QAOrchestrator")
        sm = TaskStateMachine(self.db, task_memory=self.task_memory)
        return QAOrchestrator(
            sm,
            loop_counter,
            escalation_ladder,
            self._llm_client,
            self.db,
            task_memory=self.task_memory,
        )

    async def orchestrate_qa(
        self,
        task_id: uuid.UUID,
        code: str,
        test_code: str,
        qa_agent: QAAgent,
        original_agent_id: str,
        development_phase: str,
        *,
        page_spec: PageSpec | None = None,
        loop_counter: LoopCounter | None = None,
        escalation_ladder: EscalationLadder | None = None,
    ) -> QADecision:
        """Run QA review and approve/reject via ``QAOrchestrator``."""
        _ = page_spec
        if loop_counter is None or escalation_ladder is None:
            loop_counter = LoopCounter()
            escalation_ladder = EscalationLadder(
                loop_counter, EscalationPersistence(self.db)
            )
        orchestrator = self.build_qa_orchestrator(loop_counter, escalation_ladder)
        await qa_agent.begin_review(task_id)
        runner_output = await qa_agent.review(
            task_id,
            code=code,
            test_code=test_code,
            development_phase=development_phase,
        )
        return await orchestrator.process_result(
            str(task_id),
            runner_output,
            qa_agent.agent_id,
            original_agent_id,
            development_phase,
        )

    async def handle_qa_rejection(
        self,
        task_id: uuid.UUID,
        defect_report: DefectReport,
        original_agent: FrontendAgent | BackendAgent,
    ) -> None:
        """Persist defect context for the implementer to consume on retry."""
        if self.task_memory is not None:
            await self.task_memory.set(
                str(task_id),
                "defect_report",
                defect_report.model_dump_json(),
            )
        await self.log_agent_lifecycle_event(
            agent_id=original_agent.agent_id,
            agent_role=getattr(original_agent, "agent_role", "implementer"),
            event_type="qa_rejection",
            created_by=self.agent_id,
            development_phase="FRONTEND_PHASE",
        )
        logger.warning(
            "Lead reassigned task=%s to %s after QA rejection",
            task_id,
            original_agent.agent_id,
        )

    async def run_frontend_phase(
        self,
        frontend_agents: list[FrontendAgent],
        qa_agent: QAAgent,
        layout_spec: LayoutSpecification,
        navigation_contract: NavigationContract,
        project_id: uuid.UUID,
        *,
        loop_counter: LoopCounter | None = None,
        escalation_ladder: EscalationLadder | None = None,
        development_phase: str = "FRONTEND_PHASE",
    ) -> FrontendPhaseResult:
        """Execute frontend tasks with full QA approve/reject loops."""
        started = time.monotonic()
        if loop_counter is None:
            loop_counter = LoopCounter()
        if escalation_ladder is None:
            escalation_ladder = EscalationLadder(
                loop_counter, EscalationPersistence(self.db)
            )

        fe_by_id = {a.agent_id: a for a in frontend_agents}
        root_title = "Build AppLayout — shared shell, NavBar, Footer"
        res = await self.db.execute(
            select(Task).where(Task.project_id == project_id)
        )
        all_tasks = list(res.scalars())
        frontend_tasks = [
            t
            for t in all_tasks
            if t.title == root_title
            or "page" in t.title.lower()
            or "AppLayout" in t.title
        ]
        root_tasks = [t for t in frontend_tasks if t.title == root_title]
        other_tasks = [t for t in frontend_tasks if t.title != root_title]

        completed: list[str] = []
        qa_cycles = 0
        agents_used: set[str] = set()
        components_registered: set[str] = set()

        async def _run_task_cycle(task: Task, page_spec: PageSpec) -> None:
            nonlocal qa_cycles
            agent = fe_by_id.get(task.assigned_agent) or frontend_agents[0]
            agents_used.add(agent.agent_id)
            if task.current_state == TaskState.PHASE_LOCKED:
                await self.approve_phase_transition(task.id)
            if task.current_state == TaskState.TODO:
                await self.assign_task(task.id)
            await agent.complete_work(
                task.id,
                task.description or task.title,
                page_spec,
                loop_count=0,
            )
            hist = TaskStateMachine(self.db, task_memory=self.task_memory)
            hrows = await hist.get_history(task.id)
            meta = hrows[-1].metadata_ or {}
            react_code = str(meta.get(KEY_WORK_OUTPUT, ""))
            test_code = str(
                (meta.get(KEY_METADATA) or {}).get("frontend_test_code")
                or "def test_ui_present():\n    assert isinstance(GENERATED_UI, str)\n"
            )
            if development_phase == "FRONTEND_PHASE":
                bundle = react_code
                pw_tests = await qa_agent.generate_playwright_tests(
                    page_spec, navigation_contract
                )
                test_payload = pw_tests
            else:
                bundle = f"GENERATED_UI = {json.dumps(react_code)}\n"
                test_payload = test_code

            while True:
                qa_cycles += 1
                decision = await self.orchestrate_qa(
                    task.id,
                    bundle,
                    test_payload,
                    qa_agent,
                    agent.agent_id,
                    development_phase,
                    page_spec=page_spec,
                    loop_counter=loop_counter,
                    escalation_ladder=escalation_ladder,
                )
                if decision.approved:
                    completed.append(str(task.id))
                    break
                if decision.escalated:
                    break
                if decision.defect_report:
                    await self.handle_qa_rejection(
                        task.id, decision.defect_report, agent
                    )
                await agent.complete_work(
                    task.id,
                    (decision.defect_report.suggestions if decision.defect_report else task.title),
                    page_spec,
                    loop_count=qa_cycles,
                )
                hrows = await hist.get_history(task.id)
                meta = hrows[-1].metadata_ or {}
                react_code = str(meta.get(KEY_WORK_OUTPUT, ""))
                bundle = react_code if development_phase == "FRONTEND_PHASE" else (
                    f"GENERATED_UI = {json.dumps(react_code)}\n"
                )

        for root in root_tasks:
            page = next((p for p in layout_spec.pages if p.route == "/"), layout_spec.pages[0])
            await _run_task_cycle(root, page)
            await self.unlock_dependent_tasks(root_title, project_id)

        for task in other_tasks:
            page = next(
                (p for p in layout_spec.pages if p.name.lower() in task.title.lower()),
                layout_spec.pages[0],
            )
            await _run_task_cycle(task, page)

        reg = ComponentRegistry(self.db)
        for entry in await reg.list_all(str(project_id)):
            components_registered.add(entry.component_name)

        return FrontendPhaseResult(
            project_id=str(project_id),
            completed_tasks=completed,
            total_tasks=len(frontend_tasks),
            qa_cycles=qa_cycles,
            components_registered=sorted(components_registered),
            agents_used=sorted(agents_used),
            phase_duration_seconds=time.monotonic() - started,
        )

    async def execute_human_gate(
        self,
        frontend_phase_result: FrontendPhaseResult,
        component_registry: ComponentRegistry,
        navigation_contract: NavigationContract,
        api_contract: dict,
        project_id: uuid.UUID,
        human_approval_callback: Callable[[str], Awaitable[bool]],
    ) -> PhaseGateResult:
        """Compile report, review API contract, present human gate, unlock backend."""
        if self._llm_client is None:
            raise RuntimeError("LeadAgent needs llm_client for human gate")
        phase_gate = PhaseGate(self, self._llm_client, self.db)
        report = await phase_gate.compile_report(
            frontend_phase_result,
            component_registry,
            navigation_contract,
            str(project_id),
        )
        contract_review = await phase_gate.review_api_contract(
            api_contract,
            frontend_phase_result,
            str(project_id),
        )
        if contract_review.requires_update:
            await self.write_to_project_memory(
                "api_contract",
                contract_review.updated_contract,
                project_id=project_id,
            )
        result = await phase_gate.present_to_human(report, human_approval_callback)
        result.api_contract_updated = contract_review.requires_update
        await self.log_agent_lifecycle_event(
            agent_id=self.agent_id,
            agent_role="lead_agent",
            event_type="human_gate_presented",
            created_by=self.agent_id,
            project_id=project_id,
            development_phase="HUMAN_GATE",
        )
        if result.approved:
            await self._unlock_backend_tasks(project_id)
            await self.log_agent_lifecycle_event(
                agent_id=self.agent_id,
                agent_role="lead_agent",
                event_type="phase_transition_approved",
                created_by=self.agent_id,
                project_id=project_id,
                development_phase="BACKEND_PHASE",
            )
        else:
            await self._create_feedback_tasks(result.feedback, project_id)
        return result

    async def _unlock_backend_tasks(self, project_id: uuid.UUID) -> int:
        res = await self.db.execute(
            select(Task).where(
                Task.project_id == project_id,
                Task.current_state == TaskState.PHASE_LOCKED,
            )
        )
        tasks = list(res.scalars())
        count = 0
        for task in tasks:
            await self.approve_phase_transition(task.id)
            count += 1
        logger.info(
            "Unlocked %d backend task(s) for project %s",
            count,
            project_id,
        )
        return count

    async def _create_feedback_tasks(
        self,
        feedback: str,
        project_id: uuid.UUID,
    ) -> list[Task]:
        """Create follow-up frontend tasks when the human gate is not approved."""
        title = f"Address gate feedback: {feedback[:80]}"
        task = await self.create_task(
            title=title,
            description=feedback,
            complexity=TaskComplexity.LOW,
            assigned_agent="frontend_agent_1",
            project_id=project_id,
        )
        await self.log_agent_lifecycle_event(
            agent_id=self.agent_id,
            agent_role="lead_agent",
            event_type="feedback_task_created",
            created_by=self.agent_id,
            project_id=project_id,
            development_phase="FRONTEND_PHASE",
        )
        return [task]
