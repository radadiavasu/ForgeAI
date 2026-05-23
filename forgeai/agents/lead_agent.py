"""Lead agent: task creation, lifecycle transitions, project artefacts."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
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
from forgeai.bootstrap.schemas import (
    AgentRecommendation,
    ApprovedConfig,
    BootstrapResult,
    TaskPlan,
    TaskSpec,
)
from forgeai.contracts.navigation import NavigationNegotiator
from forgeai.contracts.registry import ComponentRegistry
from forgeai.contracts.schemas import LayoutSpecification, NavigationContract, PageSpec
from forgeai.escalation.ladder import EscalationLadder
from forgeai.escalation.loop_counter import LoopCounter
from forgeai.escalation.persistence import EscalationPersistence
from forgeai.orchestration.phase_gate import PhaseGate
from forgeai.orchestration.qa_loop import QAOrchestrator
from forgeai.intelligence.schemas import FinalReviewResult
from forgeai.lifecycle.schemas import ChangeDecision, ChangeHistoryEntry
from forgeai.orchestration.schemas import (
    BackendPhaseResult,
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

ROOT_LAYOUT_TASK_TITLE = "Build AppLayout — shared shell, NavBar, Footer"


def _backend_endpoint_task_title(method: str, path: str) -> str:
    """Title for a backend task tied to one API surface."""
    verb = (method or "GET").strip().upper()
    route = (path or "/").strip()
    if route and not route.startswith("/"):
        route = f"/{route}"
    return f"Implement {verb} {route}"


def _backend_tasks_from_master_doc(master_doc: MasterDocument) -> list[TaskSpec]:
    """One backend task per API surface in the Master_Document."""
    tasks: list[TaskSpec] = []
    for surface in master_doc.api_surfaces:
        title = f"Implement {surface.method} {surface.endpoint}"
        desc = (surface.description or "").strip() or title
        tasks.append(
            TaskSpec(
                title=title,
                description=desc,
                complexity="MEDIUM",
                phase="BACKEND_PHASE",
                dependencies=[],
            )
        )
    return tasks


def _numbered_backend_tasks(count: int) -> list[TaskSpec]:
    """Fallback when Master_Document defines no API surfaces."""
    return [
        TaskSpec(
            title=f"Backend task {i}",
            description="Phase-locked backend work",
            complexity="LOW",
            phase="BACKEND_PHASE",
            dependencies=[],
        )
        for i in range(1, max(count, 1) + 1)
    ]


def _frontend_tasks_from_navigation(nav: NavigationContract) -> list[TaskSpec]:
    """Frontend tasks from Navigation_Contract component names."""
    tasks: list[TaskSpec] = [
        TaskSpec(
            title=ROOT_LAYOUT_TASK_TITLE,
            description="Root layout and shared navigation shell.",
            complexity="MEDIUM",
            phase="FRONTEND_PHASE",
            dependencies=[],
        )
    ]
    seen: set[str] = set()
    layout_key = (nav.shared_layout_component or "AppLayout").strip().lower()
    for route in nav.routes:
        comp = (route.component_name or "").strip()
        if not comp:
            continue
        key = comp.lower()
        if route.is_root_layout or key == layout_key:
            continue
        if key in seen:
            continue
        seen.add(key)
        tasks.append(
            TaskSpec(
                title=f"Build {comp} component",
                description=f"Implement {comp} for route {route.path}.",
                complexity="LOW",
                phase="FRONTEND_PHASE",
                dependencies=[ROOT_LAYOUT_TASK_TITLE],
            )
        )
    return tasks


def _complexity_distribution(*task_groups: list[TaskSpec]) -> dict[str, int]:
    dist = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for group in task_groups:
        for spec in group:
            level = (spec.complexity or "LOW").upper()
            if level in dist:
                dist[level] += 1
    return dist


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

    async def _llm_decompose_task_plan(
        self,
        master_doc: MasterDocument,
        *,
        frontend_only: bool = False,
    ) -> TaskPlan | None:
        """LLM task decomposition; returns None on parse failure."""
        if self._llm_client is None:
            return None
        if frontend_only:
            user_message = (
                "Decompose frontend work from this Master_Document into JSON with key "
                "frontend_tasks (array). Each task: title, description, complexity "
                "(LOW|MEDIUM|HIGH), phase (FRONTEND_PHASE), dependencies (task titles). "
                "Include a root task titled exactly "
                f"'{ROOT_LAYOUT_TASK_TITLE}' with no dependencies. "
                "Page tasks should be titled 'Build {ComponentName} component'.\n\n"
                f"{master_doc.model_dump_json()}"
            )
        else:
            user_message = (
                "Decompose the following Master_Document into a JSON object with keys: "
                "frontend_tasks, backend_tasks, total_tasks, estimated_complexity_distribution.\n"
                "Each task must have: title, description, complexity (LOW|MEDIUM|HIGH), "
                "phase (FRONTEND_PHASE|BACKEND_PHASE), dependencies (array of task titles).\n"
                "Include a root frontend task titled exactly "
                f"'{ROOT_LAYOUT_TASK_TITLE}' with no dependencies. "
                "All other FRONTEND_PHASE tasks must list that title in dependencies.\n"
                "Backend task titles must name the HTTP method and path "
                '(e.g. "Implement GET /tasks endpoint").\n\n'
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
            if frontend_only:
                items = raw.get("frontend_tasks", [])
                frontend_tasks = [
                    TaskSpec.model_validate(item)
                    for item in items
                    if isinstance(item, dict)
                ]
                return TaskPlan(frontend_tasks=frontend_tasks)
            return TaskPlan.model_validate(raw)
        except Exception:
            logger.warning("[LEAD] TaskPlan parse failed")
            return None

    async def decompose_tasks(
        self,
        master_doc: MasterDocument,
        *,
        navigation_contract: NavigationContract | None = None,
    ) -> TaskPlan:
        """Decompose work into a task plan with specific titles from project artefacts."""
        default_plan = AgentBootstrapProtocol.default_task_plan()
        use_api_backend = bool(master_doc.api_surfaces)
        use_nav_frontend = navigation_contract is not None

        llm_plan: TaskPlan | None = None
        if self._llm_client is not None and (not use_api_backend or not use_nav_frontend):
            llm_plan = await self._llm_decompose_task_plan(
                master_doc,
                frontend_only=use_api_backend and not use_nav_frontend,
            )

        if use_api_backend:
            backend_tasks = _backend_tasks_from_master_doc(master_doc)
        elif llm_plan is not None and llm_plan.backend_tasks:
            backend_tasks = llm_plan.backend_tasks
        elif default_plan.backend_tasks:
            backend_tasks = list(default_plan.backend_tasks)
        else:
            backend_tasks = _numbered_backend_tasks(1)

        if use_nav_frontend:
            frontend_tasks = _frontend_tasks_from_navigation(navigation_contract)
        elif llm_plan is not None and llm_plan.frontend_tasks:
            frontend_tasks = llm_plan.frontend_tasks
        else:
            frontend_tasks = list(default_plan.frontend_tasks)

        total = len(frontend_tasks) + len(backend_tasks)
        return TaskPlan(
            frontend_tasks=frontend_tasks,
            backend_tasks=backend_tasks,
            total_tasks=total,
            estimated_complexity_distribution=_complexity_distribution(
                frontend_tasks, backend_tasks
            ),
        )

    async def _load_master_document_for_project(
        self, project_id: uuid.UUID
    ) -> MasterDocument | None:
        row = (
            await self.db.execute(
                select(ProjectArtefactModel).where(
                    ProjectArtefactModel.project_id == project_id,
                    ProjectArtefactModel.artefact_type == "master_document",
                    ProjectArtefactModel.is_current.is_(True),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return MasterDocument.model_validate(row.content)

    async def create_backend_tasks_from_master_document(
        self,
        project_id: uuid.UUID,
        master_document: MasterDocument,
        *,
        assigned_agent: str = "backend_agent_1",
    ) -> list[Task]:
        """Create one PHASE_LOCKED backend task per Master_Document API surface."""
        if not master_document.api_surfaces:
            return []

        created: list[Task] = []
        for spec in _backend_tasks_from_master_doc(master_document):
            exists = (
                await self.db.execute(
                    select(Task).where(
                        Task.project_id == project_id,
                        Task.title == spec.title,
                    )
                )
            ).scalar_one_or_none()
            if exists is not None:
                continue
            task = await self.create_task(
                title=spec.title,
                description=spec.description,
                complexity=TaskComplexity[spec.complexity],
                assigned_agent=assigned_agent,
                project_id=project_id,
            )
            created.append(task)
        return created

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
        api_contract: dict | None = None,
        task_description: str | None = None,
        agent_role: str | None = None,
        confidence_scorer: Any | None = None,
        peer_reviewer: Any | None = None,
    ) -> QADecision:
        """Run QA review and approve/reject via ``QAOrchestrator``."""
        _ = page_spec
        if (
            confidence_scorer is not None
            and peer_reviewer is not None
            and task_description
            and agent_role
        ):
            confidence = await confidence_scorer.score(
                str(task_id),
                original_agent_id,
                agent_role,
                task_description,
                code,
            )
            if confidence_scorer.needs_peer_review(confidence, agent_role):
                peer = await peer_reviewer.review(
                    str(task_id),
                    task_description,
                    code,
                    original_agent_id,
                    f"peer_{agent_role}_1",
                )
                if not peer.approved:
                    from datetime import UTC, datetime

                    from forgeai.orchestration.schemas import DefectReport

                    return QADecision(
                        task_id=str(task_id),
                        approved=False,
                        defect_report=DefectReport(
                            task_id=str(task_id),
                            agent_id=original_agent_id,
                            original_agent_id=original_agent_id,
                            failure_summary="Peer review rejected before QA gate",
                            failed_tests=[],
                            passed_tests=[],
                            execution_mode="peer_review",
                            suggestions=peer.feedback,
                            retry_count=0,
                            created_at=datetime.now(UTC),
                        ),
                    )
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
            api_contract=api_contract,
            task_description=task_description,
        )
        contract_violation = (runner_output.sandbox_error or "").startswith(
            "API contract violation"
        )
        decision = await orchestrator.process_result(
            str(task_id),
            runner_output,
            qa_agent.agent_id,
            original_agent_id,
            development_phase,
        )
        return decision.model_copy(
            update={
                "contract_violation": contract_violation and not decision.approved,
                "tests_passed": runner_output.passed_tests,
                "tests_total": runner_output.total_tests,
            }
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
        master = await self._load_master_document_for_project(project_id)
        if master is not None:
            n = len(
                await self.create_backend_tasks_from_master_document(
                    project_id, master
                )
            )
            if n:
                logger.info(
                    "[LEAD] Created %d backend task(s) from Master_Document API surfaces",
                    n,
                )
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

    async def _set_phase(self, phase: str, project_id: uuid.UUID) -> None:
        await self.write_to_project_memory(
            "current_phase",
            {"phase": phase},
            project_id=project_id,
        )
        await self.log_agent_lifecycle_event(
            agent_id=self.agent_id,
            agent_role="lead_agent",
            event_type="phase_transition",
            created_by=self.agent_id,
            project_id=project_id,
            development_phase=phase,
        )
        logger.info("[LEAD] Phase set to %s for project %s", phase, project_id)

    async def execute_backend_gate(
        self,
        backend_result: BackendPhaseResult,
        project_id: uuid.UUID,
        human_approval_callback: Callable[[str], Awaitable[bool]],
    ) -> PhaseGateResult:
        """Compile backend report, present human gate, advance to FINAL_REVIEW."""
        if self._llm_client is None:
            raise RuntimeError("LeadAgent needs llm_client for backend gate")
        phase_gate = PhaseGate(self, self._llm_client, self.db)
        report = await phase_gate.compile_backend_report(backend_result, str(project_id))
        formatted = phase_gate.format_backend_report_for_human(report, backend_result)
        print(formatted)
        approved = await human_approval_callback(formatted)
        if approved:
            result = PhaseGateResult(approved=True, approved_at=datetime.now(UTC))
            await self._set_phase("FINAL_REVIEW", project_id)
            await self.log_agent_lifecycle_event(
                agent_id=self.agent_id,
                agent_role="lead_agent",
                event_type="backend_gate_approved",
                created_by=self.agent_id,
                project_id=project_id,
                development_phase="FINAL_REVIEW",
            )
            return result
        return PhaseGateResult(
            approved=False,
            feedback="Human requested additional backend changes before final review.",
        )

    async def execute_final_review(
        self,
        project_id: uuid.UUID,
        master_document: MasterDocument,
    ) -> FinalReviewResult:
        """Holistic review of DONE tasks against Master_Document (Phase 9 stub)."""
        if self._llm_client is None:
            raise RuntimeError("LeadAgent needs llm_client for final review")
        from forgeai.intelligence.final_review import FinalReviewer

        reviewer = FinalReviewer(self._llm_client, self.db)
        return await reviewer.review(str(project_id), master_document)

    async def deliver_project(
        self,
        project_id: str,
        output_dir: str,
        human_approval_callback: Callable[[str], Awaitable[bool]],
        *,
        master_document: MasterDocument | None = None,
        tech_stack_document: TechStackDocument | None = None,
        qa_agent: QAAgent | None = None,
    ):
        """Final review, assemble deployment package, and transition to LIVE on approval."""
        from pathlib import Path

        from forgeai.delivery.git_manager import GitManager
        from forgeai.delivery.package_assembler import PackageAssembler, DEFAULT_OUTPUT_ROOT
        from forgeai.delivery.schemas import DeploymentPackage
        from forgeai.lifecycle.project_registry import ProjectRegistry
        from forgeai.models.project_artefact import ProjectArtefactModel

        if self._llm_client is None:
            raise RuntimeError("LeadAgent needs llm_client for deliver_project")

        pid = uuid.UUID(project_id)
        master = master_document
        tech = tech_stack_document
        if master is None or tech is None:
            for artefact_type, target in (
                ("master_document", "master"),
                ("tech_stack_document", "tech"),
            ):
                row = (
                    await self.db.execute(
                        select(ProjectArtefactModel).where(
                            ProjectArtefactModel.project_id == pid,
                            ProjectArtefactModel.artefact_type == artefact_type,
                            ProjectArtefactModel.is_current.is_(True),
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    continue
                if target == "master":
                    master = MasterDocument.model_validate(row.content)
                else:
                    tech = TechStackDocument.model_validate(row.content)
        if master is None or tech is None:
            raise RuntimeError("deliver_project requires master and tech stack documents")

        print("[DELIVERY] Running final review...")
        review = await self.execute_final_review(pid, master)
        n_checked = len(review.consistency_checks)
        if review.gaps_found:
            print(f"[FINAL REVIEW] {n_checked} tasks checked — gaps found")
            for gap in review.gaps_found:
                print(f"  Gap: {gap}")
            for title in review.remediation_tasks:
                await self.create_task(
                    title=title[:512],
                    description=title,
                    complexity=TaskComplexity.MEDIUM,
                    assigned_agent="frontend_agent_1",
                    project_id=pid,
                )
        else:
            print(f"[FINAL REVIEW] {n_checked} tasks checked — no gaps ✓")

        out_path = Path(output_dir) if output_dir else DEFAULT_OUTPUT_ROOT / project_id
        git = GitManager(str(out_path))
        qa = qa_agent or QAAgent(
            "qa_delivery",
            self.db,
            llm_client=self._llm_client,
        )
        assembler = PackageAssembler(
            self.db,
            git,
            qa,
            self._llm_client,
            lead_agent=self,
        )
        print("[DELIVERY] Assembling Deployment_Package...")
        package = await assembler.assemble(
            project_id,
            master,
            tech,
            str(out_path),
            project_brief=master.project_summary,
        )

        print("")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(" DELIVERY READY")
        print("")
        print(" Your project is packaged and ready to deploy.")
        print("")
        print(f" Location: {package.output_dir}")
        print(f" Git tag: {package.release_tag}")
        build_label = "verified ✓" if package.docker_build_passed else "failed"
        print(f" Docker build: {build_label}")
        print("")
        print(" To deploy:")
        print(f"   cd {out_path.name}")
        print("   cp .env.example .env")
        print("   docker compose up")
        print("")
        print(" Approve delivery →")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        summary = (
            f"Deliver {master.project_name} to LIVE? "
            f"Package at {package.output_dir}"
        )
        approved = await human_approval_callback(summary)
        if approved:
            print("[DELIVERY] Human approved")
            registry = ProjectRegistry(self.db)
            try:
                await registry.set_live(project_id, package.release_tag)
                print("[REGISTRY] Project: ACTIVE → LIVE")
            except ValueError:
                project = await registry.get_project(project_id)
                if project is not None and project.status.value == "LIVE":
                    print("[REGISTRY] Project already LIVE")
                else:
                    raise
            await self.log_agent_lifecycle_event(
                agent_id=self.agent_id,
                agent_role="lead_agent",
                event_type="delivery_approved",
                created_by=self.agent_id,
                project_id=pid,
                development_phase="LIVE",
            )
        print("[DELIVERY] Package complete")
        print("")
        print("--- FINAL SUMMARY ---")
        print(f"Project: {master.project_name}")
        print(f"Tasks completed: {package.git_log and len(package.files_written) or 0}")
        print(f"Release: {package.release_tag}")
        return package

    async def enter_live_mode(self, project_id: uuid.UUID, release_tag: str) -> None:
        """Destroy execution agents and transition project to LIVE dormancy."""
        from forgeai.lifecycle.project_registry import ProjectRegistry

        registry = ProjectRegistry(self.db)
        await registry.set_live(str(project_id), release_tag)
        await self.log_agent_lifecycle_event(
            agent_id=self.agent_id,
            agent_role="lead_agent",
            event_type="live_mode_entered",
            created_by=self.agent_id,
            project_id=project_id,
            development_phase="LIVE",
        )
        logger.info(
            "[LEAD] Dormancy entered — project %s is LIVE (release=%s)",
            project_id,
            release_tag,
        )

    async def accept_change_request(
        self,
        change_request: str,
        project_id: uuid.UUID,
        master_document: MasterDocument,
        human_approval_callback: Callable[[str], Awaitable[ChangeDecision]],
        *,
        human_scope_callback: Callable[[object], Awaitable[bool]] | None = None,
    ) -> ChangeHistoryEntry:
        """Classify, analyse, and execute a change on a LIVE project."""
        from forgeai.lifecycle.change_classifier import ChangeClassifier
        from forgeai.lifecycle.change_executor import ChangeExecutor, handle_architectural
        from forgeai.lifecycle.change_history import write_change_history
        from forgeai.lifecycle.impact_analyser import ImpactAnalyser
        from forgeai.lifecycle.patch_executor import PatchExecutor
        from forgeai.lifecycle.project_registry import ProjectRegistry
        from forgeai.lifecycle.schemas import (
            ChangeDecision,
            ChangeHistoryEntry,
            ChangeType,
            HumanChangeApproval,
            ProjectStatus,
            RiskLevel,
        )

        if self._llm_client is None:
            raise RuntimeError("LeadAgent needs llm_client for change requests")

        registry = ProjectRegistry(self.db)
        project = await registry.get_project(str(project_id))
        if project is None or project.status != ProjectStatus.LIVE:
            raise RuntimeError("Change requests require a LIVE project")

        classifier = ChangeClassifier(self._llm_client)
        classification = await classifier.classify(
            change_request,
            master_document,
            ProjectStatus.LIVE,
        )
        print(
            f"[CLASSIFIER] {classification.change_type.value} | "
            f"Risk: {classification.risk_level.value}"
        )

        analyser = ImpactAnalyser(self._llm_client, self.db)
        impact = await analyser.analyse(
            change_request,
            classification,
            str(project_id),
            master_document,
        )
        print(
            f"[IMPACT] {len(impact.affected_task_ids)} task(s) affected | "
            f"~${impact.estimated_cost_usd:.2f} | ~{impact.estimated_time_minutes} min"
        )

        if classification.risk_level == RiskLevel.ARCHITECTURAL:
            decision = await handle_architectural(
                impact, str(project_id), human_approval_callback
            )
            if decision not in ChangeDecision.__members__:
                decision = ChangeDecision.REJECT
        elif classification.requires_human_confirmation:
            print(impact.human_message)
            decision = await human_approval_callback(impact.human_message)
        else:
            print("[CLASSIFIER] LOW risk — auto-proceeding")
            decision = ChangeDecision.PROCEED

        approval = HumanChangeApproval(
            project_id=str(project_id),
            change_request=change_request,
            impact_analysis=impact,
            decision=decision,
        )

        execution_result = None
        outcome = decision.value

        if decision == ChangeDecision.PROCEED:
            if classification.change_type in (ChangeType.BUGFIX, ChangeType.SMALL_FEATURE):
                from forgeai.escalation import EscalationLadder, EscalationPersistence
                from forgeai.escalation.loop_counter import LoopCounter

                loop_counter = LoopCounter()
                ladder = EscalationLadder(loop_counter, EscalationPersistence(self.db))
                qa_orch = self.build_qa_orchestrator(loop_counter, ladder)
                patch = PatchExecutor(self, qa_orch, self.db)
                execution_result = await patch.execute(impact, approval, str(project_id))
                outcome = "PATCH_COMPLETE"
            elif classification.change_type == ChangeType.LARGE_FEATURE:
                from forgeai.escalation import EscalationLadder, EscalationPersistence
                from forgeai.escalation.loop_counter import LoopCounter

                loop_counter = LoopCounter()
                ladder = EscalationLadder(loop_counter, EscalationPersistence(self.db))
                qa_orch = self.build_qa_orchestrator(loop_counter, ladder)

                async def _scope_ok(_spec: object) -> bool:
                    if human_scope_callback is None:
                        return True
                    return await human_scope_callback(_spec)

                change_exec = ChangeExecutor(self, self._llm_client, qa_orch, self.db)
                execution_result = await change_exec.execute_change(
                    change_request,
                    approval,
                    str(project_id),
                    master_document,
                    _scope_ok,
                )
                outcome = "CHANGE_COMPLETE"
        elif decision in (ChangeDecision.QUEUE, ChangeDecision.DEFER):
            await self.write_to_project_memory(
                "queued_change",
                approval.model_dump(mode="json"),
                project_id=project_id,
            )
            outcome = f"{decision.value}_STORED"

        entry = ChangeHistoryEntry(
            entry_id=str(uuid.uuid4()),
            project_id=str(project_id),
            change_request=change_request,
            classification=classification,
            impact_analysis=impact,
            human_decision=approval,
            execution_result=execution_result,
            outcome=outcome,
        )
        await write_change_history(self, entry)
        print("[HISTORY] Change written to Project_Memory")
        return entry

    async def archive_project(self, project_id: uuid.UUID) -> None:
        """Human-triggered LIVE → ARCHIVED transition."""
        from forgeai.lifecycle.project_registry import ProjectRegistry

        registry = ProjectRegistry(self.db)
        await registry.set_archived(str(project_id))
        await self.log_agent_lifecycle_event(
            agent_id=self.agent_id,
            agent_role="lead_agent",
            event_type="project_archived",
            created_by=self.agent_id,
            project_id=project_id,
            development_phase="ARCHIVED",
        )
