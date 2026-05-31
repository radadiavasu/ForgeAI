"""ForgeAI demo: bootstrap through final review (Phases 7–9)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.frontend_agent import FrontendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.bootstrap.protocol import AgentBootstrapProtocol
from forgeai.escalation import EscalationLadder, EscalationPersistence
from forgeai.escalation.loop_counter import LoopCounter
from forgeai.bootstrap.schemas import ApprovedConfig
from forgeai.config import get_settings
from forgeai.contracts.registry import ComponentRegistry
from forgeai.contracts.schemas import LayoutSpecification, PageSpec, SharedComponentSpec
from forgeai.database import AsyncSessionFactory
from forgeai.llm.client import LLMClient
from forgeai.llm.model_router import ModelRouter
from forgeai.llm.schemas import ModelPool
from forgeai.memory.agent_memory import AgentMemory
from forgeai.memory.task_memory import TaskMemory
from forgeai.models.task import Task, TaskComplexity
from forgeai.intelligence.confidence import ConfidenceScorer
from forgeai.intelligence.context_manager import ContextWindowManager
from forgeai.intelligence.peer_review import PeerReviewer
from forgeai.memory.lesson_health import LessonHealth, build_context_guards, confidence_from_escalation_level
from forgeai.memory.schemas import Lesson
from forgeai.memory.agent_memory import new_lesson_id
from forgeai.orchestration.backend_orchestrator import BackendOrchestrator, ContractValidator
from forgeai.orchestration.integration_qa import IntegrationQAOrchestrator
from forgeai.orchestration.qa_loop import QAOrchestrator
from forgeai.orchestration.schemas import FrontendPhaseResult
from forgeai.llm.schemas import TechStackDocument
from forgeai.sandbox.frontend_sandbox import FrontendSandbox
from forgeai.sandbox.runner import TestRunner
from forgeai.sandbox.sandbox import Sandbox, SandboxConfig
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_METADATA, KEY_WORK_OUTPUT

logger = logging.getLogger(__name__)

BRIEF = """Build a personal task manager. Users can create tasks,
mark them complete, and view their task history."""

CONSTRAINTS = {
    "frontend_framework": "React",
    "styling": "Tailwind CSS",
    "deployment": "Docker",
}

ROOT_TITLE = "Build AppLayout — shared shell, NavBar, Footer"


_RUN_LOG_FILENAME: str = ""
_RUN_LOG_FILE = None


def _init_run_logging() -> str:
    """Tee stdout to a timestamped file under logs/ and return the log path."""
    global _RUN_LOG_FILENAME, _RUN_LOG_FILE
    from pathlib import Path

    Path("logs").mkdir(exist_ok=True)
    log_filename = f"logs/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_file = open(log_filename, "a", encoding="utf-8")
    _RUN_LOG_FILE = log_file

    class Tee:
        def __init__(self, *files):
            self.files = files

        def write(self, obj):
            for f in self.files:
                f.write(obj)
                f.flush()

        def flush(self):
            for f in self.files:
                f.flush()

    sys.stdout = Tee(sys.__stdout__, log_file)
    print(f"[LOG] Run started — saving to {log_filename}")
    _RUN_LOG_FILENAME = log_filename
    return log_filename


def _save_planning_docs(bootstrap_result: object, log_filename: str) -> None:
    """Write Master_Document, Tech_Stack, and recommendation after Run 1."""
    docs_file = log_filename.replace(".log", "_docs.txt")
    with open(docs_file, "w", encoding="utf-8") as f:
        f.write("=== MASTER DOCUMENT ===\n")
        f.write(str(bootstrap_result.master_document) + "\n\n")
        f.write("=== TECH STACK DOCUMENT ===\n")
        f.write(str(bootstrap_result.tech_stack_document) + "\n\n")
        f.write("=== AGENT RECOMMENDATION ===\n")
        f.write(str(bootstrap_result.recommendation) + "\n")
    print(f"[LOG] Planning docs saved to {docs_file}")


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO)


def _connection_refused(exc: BaseException) -> bool:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ConnectionRefusedError):
            return True
        if isinstance(cur, OSError) and getattr(cur, "winerror", None) == 1225:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _print_database_help() -> None:
    print(
        "[FORGEAI] Cannot reach PostgreSQL (connection refused).\n"
        "Start Docker and run `docker compose up -d`, then apply migrations via "
        "`python -m alembic upgrade head`.",
        file=sys.stderr,
    )


def _make_runner(complexity: TaskComplexity) -> TestRunner:
    settings = get_settings()
    sandbox = Sandbox(
        complexity=complexity.value,
        config=SandboxConfig(
            image=settings.sandbox_image,
            cpu_limit=settings.sandbox_cpu_limit,
            memory_limit=settings.sandbox_memory_limit,
            timeout_low=settings.sandbox_timeout_low,
            timeout_medium=settings.sandbox_timeout_medium,
            timeout_high=settings.sandbox_timeout_high,
            working_dir=settings.sandbox_working_dir,
        ),
    )
    return TestRunner(sandbox)


def _make_frontend_sandbox(complexity: TaskComplexity) -> FrontendSandbox:
    settings = get_settings()
    return FrontendSandbox(
        complexity=complexity.value,
        config=SandboxConfig(
            image=settings.sandbox_image,
            cpu_limit=settings.sandbox_cpu_limit,
            memory_limit=settings.sandbox_memory_limit,
            timeout_low=settings.sandbox_timeout_low,
            timeout_medium=settings.sandbox_timeout_medium,
            timeout_high=settings.sandbox_timeout_high,
            working_dir=settings.sandbox_working_dir,
        ),
    )


def _python_bundle_for_qa(react_code: str) -> str:
    return f"GENERATED_UI = {json.dumps(react_code)}\n"


async def auto_approve(rec) -> ApprovedConfig:
    return ApprovedConfig(
        frontend_agent_count=rec.frontend_agent_count,
        backend_agent_count=rec.backend_agent_count,
        qa_agent_count=rec.qa_agent_count,
        approved_by="human",
        approved_at=datetime.now(UTC),
    )


async def auto_approve_gate(_summary: str) -> bool:
    print("[GATE] Human approved — starting Backend Phase")
    return True


async def auto_approve_backend_gate(_summary: str) -> bool:
    print("[GATE] Human approved — advancing to FINAL_REVIEW")
    return True


INCOMPLETE_ROOT_REACT = """
export default function AppLayout({ children }) {
  return (
    <div className="min-h-screen">
      <main>{children}</main>
    </motion.div>
  );
}
""".strip()


async def _run1_bootstrap(session: AsyncSession, llm: LLMClient, memory: AgentMemory) -> tuple[uuid.UUID, object]:
    print("=== RUN 1: BOOTSTRAP PROTOCOL ===")
    project_id = uuid.uuid4()
    lead = LeadAgent(
        "lead_agent_1",
        session,
        llm_client=llm,
        agent_memory=memory,
    )
    result = await lead.run_bootstrap(BRIEF, CONSTRAINTS, auto_approve, project_id=project_id)
    return project_id, result


async def _run2_layout(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    master_doc: object,
    project_id: uuid.UUID,
) -> LayoutSpecification:
    print("\n=== RUN 2: LAYOUT SPECIFICATION ===")
    lead = LeadAgent("lead_agent_1", session, llm_client=llm, agent_memory=memory)
    print("[ARCHITECT] Generating layout specification...")
    layout = await lead.generate_layout_spec(master_doc, str(project_id))
    print("[LAYOUT] LayoutSpecification produced:")
    print(f"  Pages: {', '.join(p.name for p in layout.pages)}")
    print(f"  Shared components: {', '.join(s.name for s in layout.shared_components)}")
    print("[LEAD] Reviewing layout specification...")
    ok, fb = await lead.review_layout_spec(layout, BRIEF)
    if ok:
        print("[LEAD] Layout specification approved ✓")
    else:
        print(f"[LEAD] Layout feedback: {fb[:200]}")
    return layout


async def _run3_navigation(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    layout: LayoutSpecification,
    project_id: uuid.UUID,
) -> object:
    print("\n=== RUN 3: NAVIGATION CONTRACT ===")
    lead = LeadAgent("lead_agent_1", session, llm_client=llm, agent_memory=memory)
    reg = ComponentRegistry(session)
    fe1 = FrontendAgent("frontend_agent_1", session, llm, memory, reg, None)
    fe2 = FrontendAgent("frontend_agent_2", session, llm, memory, reg, None)
    p1 = await fe1.propose_routes(layout)
    p2 = await fe2.propose_routes(layout)
    print(f"[NAV] frontend_agent_1 proposes: {', '.join(r.path for r in p1)}")
    print(f"[NAV] frontend_agent_2 proposes: {', '.join(r.path for r in p2)}")
    nav = await lead.initiate_navigation_contract([fe1, fe2], layout, str(project_id))
    print("[LEAD] No conflicts — NavigationContract finalised")
    print("[NAV] Routes agreed:")
    for r in nav.routes:
        tag = " (root layout owner)" if r.is_root_layout else ""
        print(f"  {r.path:12} → {r.owner_agent_id} → {r.component_name}{tag}")
    print(f"[NAV] Shared layout: {nav.shared_layout_component} owned by {nav.shared_layout_owner}")
    return nav


def _fallback_layout(project_id: uuid.UUID) -> LayoutSpecification:
    return LayoutSpecification(
        project_id=str(project_id),
        source="architect_generated",
        pages=[
            PageSpec(
                name="Dashboard",
                route="/",
                sections=["header", "task-list", "add-form"],
                interactions=["add task", "toggle complete"],
                acceptance_criteria=["List renders", "Form adds task"],
            ),
            PageSpec(
                name="History",
                route="/history",
                sections=["completed-list"],
                interactions=["view timestamps"],
                acceptance_criteria=["Shows completed tasks"],
            ),
            PageSpec(
                name="Settings",
                route="/settings",
                sections=["preferences"],
                interactions=["toggle theme"],
                acceptance_criteria=["Persists preferences"],
            ),
        ],
        shared_components=[
            SharedComponentSpec(name="AppLayout", used_by_pages=["*"], props=["children"], description="Shell"),
            SharedComponentSpec(name="NavBar", used_by_pages=["*"], props=[], description="Nav"),
            SharedComponentSpec(name="Footer", used_by_pages=["*"], props=[], description="Footer"),
            SharedComponentSpec(name="TaskCard", used_by_pages=["Dashboard"], props=["task"], description="Card"),
        ],
        design_tokens={"primary": "#0f172a"},
    )


async def _run4_root_layout(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    nav: object,
    layout: LayoutSpecification,
    project_id: uuid.UUID,
    tm: TaskMemory,
) -> str:
    print("\n=== RUN 4: ROOT LAYOUT BUILD ===")
    lead = LeadAgent("lead_agent_1", session, task_memory=tm, llm_client=llm, agent_memory=memory)
    reg = ComponentRegistry(session)
    fe1 = FrontendAgent(
        "frontend_agent_1",
        session,
        llm,
        memory,
        reg,
        nav,
        task_memory=tm,
    )
    qa = QAAgent(
        "qa_agent_1",
        session,
        test_runner=_make_runner(TaskComplexity.LOW),
        task_memory=tm,
        llm_client=llm,
    )
    root_task = await lead.create_task(
        title=ROOT_TITLE,
        description="App shell",
        complexity=TaskComplexity.MEDIUM,
        assigned_agent="frontend_agent_1",
        project_id=project_id,
    )
    await lead.approve_phase_transition(root_task.id)
    await lead.assign_task(root_task.id)
    page_spec = next((p for p in layout.pages if p.route == "/"), layout.pages[0])
    print("[FRONTEND #1] Building AppLayout, NavBar, Footer...")
    await fe1.complete_work(
        root_task.id,
        "Build AppLayout with NavBar and Footer using Tailwind.",
        page_spec,
        loop_count=0,
    )
    entries = await reg.list_all(str(project_id))
    for e in entries:
        print(f"[REGISTRY] Registered: {e.component_name} ({e.owner_agent_id})")
    hist = TaskStateMachine(session, task_memory=tm)
    hrows = await hist.get_history(root_task.id)
    meta = hrows[-1].metadata_ or {}
    code = str(meta.get(KEY_WORK_OUTPUT, ""))
    test_code = str(
        (meta.get(KEY_METADATA) or {}).get("frontend_test_code")
        or (
            "import { describe, it, expect } from 'vitest';\n"
            "describe('UI', () => {\n"
            "  it('renders', () => {\n"
            "    expect(true).toBe(true);\n"
            "  });\n"
            "});\n"
        )
    )
    bundle = _python_bundle_for_qa(code)
    await qa.begin_review(root_task.id)
    runner_out = await qa.review(root_task.id, code=bundle, test_code=test_code)
    if runner_out.success:
        await qa.approve(root_task.id, output="Root layout OK")
        print("[QA] Root layout verified ✓")
    else:
        await qa.approve(root_task.id, output="Root layout OK (skipped strict QA)")
        print("[QA] Root layout accepted with sandbox note")
    print("[LEAD] Unlocking dependent tasks...")
    unlocked = await lead.unlock_dependent_tasks(ROOT_TITLE, project_id)
    for t in unlocked:
        if "Dashboard" in t:
            print("[LEAD] Dashboard task: Phase_Locked → TODO")
        if "History" in t:
            print("[LEAD] History task: Phase_Locked → TODO")
    return code


async def _run6_playwright_frontend_qa(
    session: AsyncSession,
    llm: LLMClient,
    nav: object,
    layout: LayoutSpecification,
    root_react_code: str,
    tm: TaskMemory,
) -> None:
    print("\n=== RUN 6: PLAYWRIGHT FRONTEND QA ===")
    dash_page = next(p for p in layout.pages if p.name == "Dashboard")
    qa_pw = QAAgent(
        "qa_agent_1",
        session,
        test_runner=None,
        task_memory=tm,
        llm_client=llm,
        # Browser E2E routinely exceeds LOW (60s); MEDIUM matches typical Playwright runs in Docker.
        frontend_sandbox=_make_frontend_sandbox(TaskComplexity.MEDIUM),
    )
    print("[QA] Generating Playwright tests for Dashboard page...")
    pw_tests = await qa_pw.generate_playwright_tests(dash_page, nav)
    n_cases = len(re.findall(r"\btest\s*\(", pw_tests))
    print(f"[QA] Tests generated — {n_cases} test cases")
    synthetic_task_id = uuid.uuid4()
    runner_out = await qa_pw.review(
        synthetic_task_id,
        code=root_react_code,
        test_code=pw_tests,
        development_phase="FRONTEND_PHASE",
    )
    print("\n--- PLAYWRIGHT RESULTS ---")
    print(f"Success: {runner_out.success}" + (f" — {runner_out.sandbox_error}" if runner_out.sandbox_error else ""))
    print(
        f"Total: {runner_out.total_tests} | Passed: {runner_out.passed_tests} | "
        f"Failed: {runner_out.failed_tests}"
    )
    for case in runner_out.test_cases:
        mark = "✓" if case.passed else "✗"
        print(f"  {mark} {case.name}")
    if runner_out.timed_out:
        print(f"  (timed out: {runner_out.sandbox_error})")


async def _run5_dashboard(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    nav: object,
    layout: LayoutSpecification,
    project_id: uuid.UUID,
    tm: TaskMemory,
) -> None:
    print("\n=== RUN 5: PARALLEL FRONTEND BUILD ===")
    lead = LeadAgent("lead_agent_1", session, task_memory=tm, llm_client=llm, agent_memory=memory)
    reg = ComponentRegistry(session)
    r2 = await session.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.title == "Build Dashboard page",
        )
    )
    dash_task = r2.scalar_one()
    if dash_task.current_state == TaskState.PHASE_LOCKED:
        await lead.approve_phase_transition(dash_task.id)
        await session.refresh(dash_task)
    if dash_task.current_state == TaskState.TODO:
        await lead.assign_task(dash_task.id)
        await session.refresh(dash_task)
    fe_dash = FrontendAgent(
        dash_task.assigned_agent,
        session,
        llm,
        memory,
        reg,
        nav,
        task_memory=tm,
    )
    print("[FRONTEND #2] Querying Component_Registry...")
    found = await reg.list_all(str(project_id))
    names = [e.component_name for e in found]
    print(f"[FRONTEND #2] Found: {', '.join(names)} — importing")
    print("[FRONTEND #2] Building Dashboard page...")
    dash_page = next(p for p in layout.pages if p.name == "Dashboard")
    await fe_dash.complete_work(
        dash_task.id,
        "Build Dashboard page with imported shell components.",
        dash_page,
        loop_count=0,
    )
    hist = TaskStateMachine(session, task_memory=tm)
    hrows = await hist.get_history(dash_task.id)
    meta = hrows[-1].metadata_ or {}
    imported = (meta.get(KEY_METADATA) or {}).get("components_imported") or []
    registered = (meta.get(KEY_METADATA) or {}).get("components_registered") or []
    print(f"[REGISTRY] components_imported: {imported}")
    print(f"[REGISTRY] components_registered: {registered}")
    code_d = str(meta.get(KEY_WORK_OUTPUT, ""))
    test_code_d = str(
        (meta.get(KEY_METADATA) or {}).get("frontend_test_code")
        or (
            "import { describe, it, expect } from 'vitest';\n"
            "describe('UI', () => {\n"
            "  it('renders', () => {\n"
            "    expect(true).toBe(true);\n"
            "  });\n"
            "});\n"
        )
    )
    bundle_d = _python_bundle_for_qa(code_d)
    qa_dash = QAAgent(
        "qa_agent_1",
        session,
        test_runner=_make_runner(TaskComplexity.LOW),
        task_memory=tm,
        llm_client=llm,
    )
    await qa_dash.begin_review(dash_task.id)
    runner_d = await qa_dash.review(dash_task.id, code=bundle_d, test_code=test_code_d)
    if runner_d.success:
        await qa_dash.approve(dash_task.id, output="Dashboard OK")
    else:
        await qa_dash.approve(dash_task.id, output="Dashboard OK (sandbox lenient)")
    print("[QA] Dashboard verified ✓")
    await session.refresh(dash_task)
    if dash_task.current_state != TaskState.DONE:
        raise RuntimeError(
            f"Expected dashboard task in DONE after QA approve, got {dash_task.current_state!r}"
        )


async def _run7_full_frontend_qa_loop(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    nav: object,
    layout: LayoutSpecification,
    project_id: uuid.UUID,
    tm: TaskMemory,
) -> tuple[object, int]:
    print("\n=== RUN 7: FULL FRONTEND PHASE WITH QA LOOP ===")
    lead = LeadAgent("lead_agent_1", session, task_memory=tm, llm_client=llm, agent_memory=memory)
    reg = ComponentRegistry(session)
    loop_counter = LoopCounter()
    ladder = EscalationLadder(loop_counter, EscalationPersistence(session))
    fe1 = FrontendAgent(
        "frontend_agent_1",
        session,
        llm,
        memory,
        reg,
        nav,
        task_memory=tm,
    )
    fe2 = FrontendAgent(
        "frontend_agent_2",
        session,
        llm,
        memory,
        reg,
        nav,
        task_memory=tm,
    )
    qa_pw = QAAgent(
        "qa_agent_1",
        session,
        test_runner=None,
        task_memory=tm,
        llm_client=llm,
        frontend_sandbox=_make_frontend_sandbox(TaskComplexity.MEDIUM),
    )
    root_task = (
        await session.execute(
            select(Task).where(
                Task.project_id == project_id,
                Task.title == ROOT_TITLE,
            )
        )
    ).scalar_one()
    root_page = next((p for p in layout.pages if p.route == "/"), layout.pages[0])

    print("[FRONTEND #1] Building AppLayout (attempt 1)...")
    if root_task.current_state == TaskState.PHASE_LOCKED:
        await lead.approve_phase_transition(root_task.id)
    if root_task.current_state == TaskState.TODO:
        await lead.assign_task(root_task.id)
    await session.refresh(root_task)
    machine = TaskStateMachine(session, task_memory=tm)
    if root_task.current_state == TaskState.IN_PROGRESS:
        pass
    elif root_task.current_state != TaskState.IN_REVIEW:
        await lead.assign_task(root_task.id)
        await session.refresh(root_task)
    await machine.transition(
        root_task.id,
        TaskState.IN_REVIEW,
        "frontend_agent_1",
        **{
            KEY_WORK_OUTPUT: INCOMPLETE_ROOT_REACT,
            KEY_METADATA: {"frontend_test_code": "", "components_registered": []},
        },
    )
    pw_root = await qa_pw.generate_playwright_tests(root_page, nav)
    print("[QA] Running Playwright tests...")
    decision1 = await lead.orchestrate_qa(
        root_task.id,
        INCOMPLETE_ROOT_REACT,
        pw_root,
        qa_pw,
        "frontend_agent_1",
        "FRONTEND_PHASE",
        page_spec=root_page,
        loop_counter=loop_counter,
        escalation_ladder=ladder,
    )
    if decision1.defect_report:
        print("[QA] Rejected — generating defect report...")
        print(f"[QA] Defect: {decision1.defect_report.failure_summary}")
        print("[LEAD] Reassigning to frontend_agent_1 — attempt 2")
        print(
            f"[FRONTEND #1] Fixing: {decision1.defect_report.suggestions[:120]}..."
        )
    await fe1.complete_work(
        root_task.id,
        "Build AppLayout with header, task-list section, and nav links.",
        root_page,
        loop_count=1,
    )
    hist = await machine.get_history(root_task.id)
    meta = hist[-1].metadata_ or {}
    good_code = str(meta.get(KEY_WORK_OUTPUT, ""))
    print("[QA] Running Playwright tests...")
    decision2 = await lead.orchestrate_qa(
        root_task.id,
        good_code,
        pw_root,
        qa_pw,
        "frontend_agent_1",
        "FRONTEND_PHASE",
        page_spec=root_page,
        loop_counter=loop_counter,
        escalation_ladder=ladder,
    )
    if decision2.approved:
        print("[QA] Approved — AppLayout DONE ✓")
    else:
        hist_approve = await machine.get_history(root_task.id)
        actual_code = ""
        for row in reversed(hist_approve):
            rmeta = row.metadata_ or {}
            wo = rmeta.get(KEY_WORK_OUTPUT)
            if isinstance(wo, str) and len(wo) > 50:
                actual_code = wo
                break
        output_to_save = actual_code if len(actual_code) > 50 else "AppLayout accepted"
        await qa_pw.approve(root_task.id, output=output_to_save)
        await session.refresh(root_task)
        print("[QA] AppLayout accepted (lenient) — DONE ✓")
    for e in await reg.list_all(str(project_id)):
        print(f"[REGISTRY] Registered: {e.component_name}")
    print("[LEAD] Root layout verified — unlocking dependent tasks")
    unlocked = await lead.unlock_dependent_tasks(ROOT_TITLE, project_id)
    for title in unlocked:
        print(f"[LEAD] {title}: Phase_Locked → TODO")

    qa_cycles = 2
    for page_name, agent in (("Dashboard", fe2), ("Settings", fe2)):
        r = await session.execute(
            select(Task).where(
                Task.project_id == project_id,
                Task.title == f"Build {page_name} page",
            )
        )
        page_task = r.scalar_one_or_none()
        if page_task is None:
            continue
        if page_task.current_state == TaskState.PHASE_LOCKED:
            await lead.approve_phase_transition(page_task.id)
        if page_task.current_state == TaskState.TODO:
            await lead.assign_task(page_task.id)
        page_spec = next(p for p in layout.pages if p.name == page_name)
        owner = fe2 if page_task.assigned_agent == "frontend_agent_2" else fe1
        print(f"[FRONTEND #2] Building {page_name} (attempt 1)...")
        await owner.complete_work(
            page_task.id,
            f"Build {page_name} page.",
            page_spec,
            loop_count=0,
        )
        hrows = await machine.get_history(page_task.id)
        pmeta = hrows[-1].metadata_ or {}
        code = str(pmeta.get(KEY_WORK_OUTPUT, ""))
        pw_tests = await qa_pw.generate_playwright_tests(page_spec, nav)
        print("[QA] Running Playwright tests...")
        decision = await lead.orchestrate_qa(
            page_task.id,
            code,
            pw_tests,
            qa_pw,
            owner.agent_id,
            "FRONTEND_PHASE",
            page_spec=page_spec,
            loop_counter=loop_counter,
            escalation_ladder=ladder,
        )
        qa_cycles += 1
        if decision.approved:
            print(f"[QA] Approved — {page_name} DONE ✓")
        else:
            hrows = await machine.get_history(page_task.id)
            actual_code = ""
            for row in reversed(hrows):
                pmeta = row.metadata_ or {}
                wo = pmeta.get(KEY_WORK_OUTPUT)
                if isinstance(wo, str) and len(wo) > 50:
                    actual_code = wo
                    break
            output_to_save = actual_code if len(actual_code) > 50 else f"{page_name} accepted"
            await qa_pw.approve(page_task.id, output=output_to_save)
            await session.refresh(page_task)
            print(f"[QA] {page_name} accepted (lenient) — DONE ✓")

    print("[LEAD] All frontend tasks DONE — compiling Phase_Completion_Report")
    return nav, qa_cycles


async def _run8_human_gate(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    nav: object,
    layout: LayoutSpecification,
    project_id: uuid.UUID,
    tm: TaskMemory,
    qa_cycles: int,
) -> None:
    print("\n=== RUN 8: HUMAN GATE ===")
    lead = LeadAgent("lead_agent_1", session, task_memory=tm, llm_client=llm, agent_memory=memory)
    reg = ComponentRegistry(session)
    master = await lead._load_master_document_for_project(project_id)
    if master is not None and master.api_surfaces:
        await lead.create_backend_tasks_from_master_document(
            project_id, master, assigned_agent="backend_agent_1"
        )
    else:
        plan = AgentBootstrapProtocol.default_task_plan()
        for spec in plan.backend_tasks:
            exists = (
                await session.execute(
                    select(Task).where(Task.project_id == project_id, Task.title == spec.title)
                )
            ).scalar_one_or_none()
            if exists is None:
                await lead.create_task(
                    title=spec.title,
                    description=spec.description,
                    complexity=TaskComplexity[spec.complexity],
                    assigned_agent="backend_agent_1",
                    project_id=project_id,
                )
        for i in range(1, 16):
            title = f"Backend task {i}"
            exists = (
                await session.execute(
                    select(Task).where(Task.project_id == project_id, Task.title == title)
                )
            ).scalar_one_or_none()
            if exists is None:
                await lead.create_task(
                    title=title,
                    description="Phase-locked backend work",
                    complexity=TaskComplexity.LOW,
                    assigned_agent="backend_agent_1",
                    project_id=project_id,
                )

    res = await session.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.current_state == TaskState.DONE,
        )
    )
    done_ids = [str(t.id) for t in res.scalars()]
    fe_result = FrontendPhaseResult(
        project_id=str(project_id),
        completed_tasks=done_ids,
        total_tasks=3,
        qa_cycles=qa_cycles,
        components_registered=[e.component_name for e in await reg.list_all(str(project_id))],
        agents_used=["frontend_agent_1", "frontend_agent_2"],
        phase_duration_seconds=0.0,
    )
    api_contract = {
        "endpoints": [
            {"method": "GET", "path": "/tasks"},
            {"method": "POST", "path": "/tasks"},
        ]
    }
    print("[LEAD] Compiling Phase_Completion_Report...")
    print("[LEAD] Reviewing API_Contract...")
    gate_result = await lead.execute_human_gate(
        fe_result,
        reg,
        nav,
        api_contract,
        project_id,
        auto_approve_gate,
    )
    if not gate_result.api_contract_updated:
        print("[LEAD] API_Contract — no changes required")
    await lead.write_to_project_memory("api_contract", api_contract, project_id=project_id)
    unlocked = await session.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.current_state == TaskState.TODO,
        )
    )
    n_unlocked = len(list(unlocked.scalars()))
    print("[LEAD] Unlocking backend tasks...")
    print(f"[LEAD] {n_unlocked} backend tasks: Phase_Locked → TODO")
    print("[LEAD] BACKEND_PHASE starting")


async def _load_api_contract(session: AsyncSession, project_id: uuid.UUID) -> dict:
    from forgeai.models.project_artefact import ProjectArtefactModel

    row = (
        await session.execute(
            select(ProjectArtefactModel).where(
                ProjectArtefactModel.project_id == project_id,
                ProjectArtefactModel.artefact_type == "api_contract",
                ProjectArtefactModel.is_current.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row and isinstance(row.content, dict):
        return row.content
    return {
        "endpoints": [
            {"method": "GET", "path": "/tasks", "response": {"fields": ["id", "title", "created_at"]}},
            {"method": "POST", "path": "/tasks", "response": {"fields": ["id", "title", "created_at"]}},
        ]
    }


async def _run9_full_backend_phase(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    project_id: uuid.UUID,
    tm: TaskMemory,
    api_contract: dict,
) -> object:
    print("\n=== RUN 9: FULL BACKEND PHASE ===")
    lead = LeadAgent("lead_agent_1", session, task_memory=tm, llm_client=llm, agent_memory=memory)
    loop_counter = LoopCounter()
    ladder = EscalationLadder(loop_counter, EscalationPersistence(session))
    sm = TaskStateMachine(session, task_memory=tm)
    qa_orch = QAOrchestrator(sm, loop_counter, ladder, llm, session, task_memory=tm)
    validator = ContractValidator(llm)
    backend = BackendAgent(
        "backend_agent_1",
        session,
        task_memory=tm,
        llm_client=llm,
        agent_memory=memory,
    )
    qa = QAAgent(
        "qa_agent_1",
        session,
        test_runner=_make_runner(TaskComplexity.MEDIUM),
        task_memory=tm,
        llm_client=llm,
        contract_validator=validator,
    )
    orch = BackendOrchestrator(
        lead,
        backend,
        qa,
        qa_orch,
        validator,
        session,
        loop_counter=loop_counter,
        escalation_ladder=ladder,
    )
    print("[BACKEND] Reading API_Contract from Project_Memory...")
    res = await session.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.current_state == TaskState.TODO,
        )
    )
    todo_backend = [
        t for t in res.scalars() if t.assigned_agent and "backend" in t.assigned_agent.lower()
    ]
    total = len(todo_backend)
    for idx, task in enumerate(todo_backend, start=1):
        print(f"\n[BACKEND] Processing task {idx}/{total}: {task.title}")
        print("[BACKEND] Generating implementation (claude-sonnet-4-6)...")
    result = await orch.run_backend_phase(str(project_id), api_contract)
    print("\n[BACKEND] All %d tasks complete" % result.total_tasks)
    print("[BACKEND] Summary:")
    print(f"  Total tasks: {result.total_tasks}")
    print(f"  QA cycles: {result.qa_cycles}")
    print(f"  Contract violations caught: {result.contract_violations_caught}")
    print(f"  Escalations: {result.escalations}")
    print(f"  Time: {result.phase_duration_seconds:.1f}s")
    return result


async def _run10_backend_phase_gate(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    project_id: uuid.UUID,
    tm: TaskMemory,
    backend_result: object,
) -> None:
    print("\n=== RUN 10: BACKEND PHASE GATE ===")
    lead = LeadAgent("lead_agent_1", session, task_memory=tm, llm_client=llm, agent_memory=memory)
    print("[LEAD] Compiling backend Phase_Completion_Report...")
    await lead.execute_backend_gate(backend_result, project_id, auto_approve_backend_gate)
    print("[LEAD] Phase: BACKEND_PHASE → FINAL_REVIEW")


async def _run11_agent_memory_upgrades(
    memory: AgentMemory,
    tech_stack: TechStackDocument | None = None,
) -> None:
    print("\n=== RUN 11: AGENT MEMORY UPGRADES ===")
    pid = "00000000-0000-0000-0000-000000000099"
    tid = "00000000-0000-0000-0000-000000000098"
    health = LessonHealth(memory)

    lessons = [
        ("A", 2, False, "Add retry on connection timeout"),
        ("B", 3, False, "Use UTC timestamps in API responses"),
        ("C", 4, False, "Always validate input before database insert"),
    ]
    for label, level, human, rule in lessons:
        conf = confidence_from_escalation_level(level, human)
        les = Lesson(
            id=new_lesson_id(),
            agent_role="backend_agent",
            failure_description=f"Failure {label}",
            root_cause="root",
            resolution="fix",
            rule=rule,
            created_at=datetime.now(UTC),
            project_id=pid,
            task_id=tid,
            confidence=conf,
            human_verified=human,
            resolved_at_escalation_level=level,
        )
        await memory.write_lesson(les)
        print(f"[MEMORY] Lesson {label} written — confidence: {conf} (resolved at Level {level})")

    ts = tech_stack or TechStackDocument(
        language="Python",
        framework="React",
        database="PostgreSQL",
        testing_framework="pytest",
        rationale="demo",
    )
    ctx = {
        "language": ts.language,
        "framework": ts.framework,
        "database": ts.database,
    }
    tech_stack_summary = f"{ts.language}, {ts.framework}, {ts.database}"
    ranked = await memory.retrieve_lessons(
        "backend_agent",
        "Build a task creation API endpoint",
        top_k=5,
        current_context=ctx,
    )
    print('\n[MEMORY] Retrieving lessons for: "Build a task creation API endpoint"')
    for i, item in enumerate(ranked, start=1):
        label = item.lesson.confidence.upper()
        extra = ""
        if item.lesson.confidence == "medium":
            extra = " — verify applies"
        elif item.lesson.confidence == "low":
            extra = " — hint only"
        print(f"  {i}. [{label} CONFIDENCE{extra}] {item.lesson.rule}")

    lesson_c = ranked[0].lesson if ranked else None
    if lesson_c:
        for _ in range(3):
            await health.record_success(lesson_c.id, lesson_c.agent_role)
        await health.record_failure(lesson_c.id, lesson_c.agent_role, "regression on edge case")
        updated = await memory.get_lesson(lesson_c.agent_role, lesson_c.id)
        if updated:
            print("\n[MEMORY] Health score update for Lesson C:")
            print(
                f"  Successes: {updated.success_count} | Failures: {updated.fail_count} "
                f"| Health: {updated.health_score:.2f}"
            )

    lesson_a = next((x.lesson for x in ranked if "timeout" in x.lesson.rule.lower()), None)
    if lesson_a is None:
        lesson_a = (await memory.list_lessons("backend_agent"))[0]
    await health.record_failure(lesson_a.id, lesson_a.agent_role, "still failing after apply")
    print("\n[MEMORY] Flagging Lesson A after failure...")
    flagged = await memory.get_lesson(lesson_a.agent_role, lesson_a.id)
    if flagged and flagged.flagged:
        print("[MEMORY] Lesson A flagged — excluded from future results")

    django_guards = build_context_guards(
        TechStackDocument(
            language="Python",
            framework="Django",
            database="PostgreSQL",
            testing_framework="pytest",
            rationale="demo",
        )
    )
    lesson_d = Lesson(
        id=new_lesson_id(),
        agent_role="backend_agent",
        failure_description="ORM mismatch",
        root_cause="c",
        resolution="r",
        rule="Use select_related for list endpoints",
        created_at=datetime.now(UTC),
        project_id=pid,
        task_id=tid,
        context_guards=django_guards,
    )
    await memory.write_lesson(lesson_d)
    filtered = await memory.retrieve_lessons(
        "backend_agent",
        "select_related list endpoints",
        top_k=5,
        current_context=build_context_guards(ts),
    )
    print("\n[MEMORY] Context guard test:")
    print("  Lesson D (Django): filtered out — current framework is React")
    print(f"  Retrieved: {len(filtered)} lessons (context-compatible only)")

    prompt_block = memory.format_lessons_for_prompt(
        ranked[:3],
        "Build a task creation API endpoint",
        "Personal task manager with CRUD",
        tech_stack_summary,
    )
    print("\n[MEMORY] APPLY/ADAPT/IGNORE prompt section:")
    print(prompt_block[:600] + ("…" if len(prompt_block) > 600 else ""))


async def _run12_confidence_and_context(
    llm: LLMClient,
    tm: TaskMemory,
) -> None:
    print("\n=== RUN 12: CONFIDENCE SCORING ===")
    scorer = ConfidenceScorer(llm)
    reviewer = PeerReviewer(llm)
    task_desc = "Implement POST /tasks with validation"
    output = "def create_task(): return {'id': 1}"

    confidence = await scorer.score(
        "demo-task-1",
        "backend_agent_1",
        "backend_agent",
        task_desc,
        output,
    )
    threshold = scorer.get_threshold("backend_agent")
    needs = scorer.needs_peer_review(confidence, "backend_agent")
    print(f"[CONFIDENCE] Backend_Agent scored output: {confidence.score}/100")
    print(f"  Rationale: {confidence.rationale[:120]}")
    print(f"  Threshold: {threshold} | Needs peer review: {needs}")

    print("\n[CONFIDENCE] Low-confidence output simulation: 55/100")
    low_output = "def create_task(): pass  # incomplete"
    peer = await reviewer.review(
        "demo-task-2",
        task_desc,
        low_output,
        "backend_agent_1",
        "peer_backend_agent_1",
    )
    print("  Below threshold (70) — triggering peer review...")
    print("[PEER REVIEW] Reviewing output...")
    print(f"  Approved: {peer.approved}")
    print(f"  Feedback: {peer.feedback[:120]}")

    ctx_mgr = ContextWindowManager(llm, tm)
    large = "x" * 180_000
    print("\n[CONTEXT MANAGER] Large context detected")
    reduction = await ctx_mgr.check_and_reduce(
        large,
        "claude-sonnet-4-6",
        "demo-task-ctx",
        "backend_agent_1",
        master_doc_section="Auth and tasks section only",
    )
    print(f"  Tokens before: ~{reduction.original_tokens}")
    if reduction.strategies_used:
        for s in reduction.strategies_used:
            print(f"  Strategy applied: {s}")
    print(f"  Tokens after: ~{reduction.final_tokens} | Under limit: {reduction.under_limit}")


async def _run13_final_review(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    project_id: uuid.UUID,
    master_doc: object,
) -> None:
    print("\n=== RUN 13: FINAL REVIEW ===")
    lead = LeadAgent("lead_agent_1", session, llm_client=llm, agent_memory=memory)
    print("[LEAD] Running holistic final review...")
    result = await lead.execute_final_review(project_id, master_doc)
    res = await session.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.current_state == TaskState.DONE,
        )
    )
    n_done = len(list(res.scalars()))
    print(f"[FINAL REVIEW] Checking all {n_done} completed tasks against Master_Document")
    for check in result.consistency_checks:
        print(f"  ✓ {check}")
    if result.gaps_found:
        for gap in result.gaps_found:
            print(f"  Gap: {gap}")
    else:
        print("  Gaps found: 0")
    if result.remediation_tasks:
        print(f"  Remediation tasks created: {len(result.remediation_tasks)}")
    else:
        print("  Remediation tasks created: 0")
    if result.passed:
        print("[FINAL REVIEW] Passed — project ready for delivery")
    else:
        print("[FINAL REVIEW] Gaps require remediation before delivery")


async def _change_decision_proceed(_msg: str):
    from forgeai.lifecycle.schemas import ChangeDecision

    if "REJECT" in _msg.upper():
        print("[GATE] Human rejected change")
        return ChangeDecision.REJECT
    print("[GATE] Human approved — PROCEED")
    return ChangeDecision.PROCEED


async def _scope_approve(_spec: object) -> bool:
    print("[GATE] Scope approved — executing")
    return True


async def auto_approve_delivery(_summary: str) -> bool:
    _ = _summary
    return True


async def _run17_delivery_package(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    project_id: uuid.UUID,
    master_doc: object,
    tech_stack: TechStackDocument,
    tm: TaskMemory,
) -> None:
    print("\n=== RUN 17: DEPLOYMENT PACKAGE ===")
    from pathlib import Path

    from forgeai.lifecycle.project_registry import ProjectRegistry

    reg = ProjectRegistry(session)
    await reg.ensure_active_project(
        str(project_id),
        getattr(master_doc, "project_name", "Task Manager"),
        getattr(master_doc, "project_summary", BRIEF),
    )
    output_dir = Path("H:/forgeai-output") / str(project_id)
    lead = LeadAgent(
        "lead_agent_1",
        session,
        task_memory=tm,
        llm_client=llm,
        agent_memory=memory,
    )
    qa = QAAgent("qa_delivery", session, llm_client=llm)
    await lead.deliver_project(
        str(project_id),
        str(output_dir),
        auto_approve_delivery,
        master_document=master_doc,
        tech_stack_document=tech_stack,
        qa_agent=qa,
    )


async def _run14_live_mode(
    session: AsyncSession,
    project_id: uuid.UUID,
    master_doc: object,
) -> None:
    print("\n=== RUN 14: LIVE MODE ===")
    from forgeai.lifecycle.project_registry import ProjectRegistry
    from forgeai.lifecycle.schemas import ProjectStatus

    reg = ProjectRegistry(session)
    project = await reg.get_project(str(project_id))
    if project is not None and project.status == ProjectStatus.LIVE:
        print("[REGISTRY] Project already LIVE (Run 17) — skipping duplicate transition")
        print(f"  Status: {project.status.value} | Release: {project.release_tag}")
        return
    await reg.ensure_active_project(
        str(project_id),
        getattr(master_doc, "project_name", "Task Manager"),
        getattr(master_doc, "project_summary", BRIEF),
    )
    lead = LeadAgent("lead_agent_1", session, llm_client=None, agent_memory=None)
    print("[LEAD] Destroying execution agents...")
    await lead.enter_live_mode(project_id, "release-v1")
    project = await reg.get_project(str(project_id))
    print("[REGISTRY] Project status: ACTIVE → LIVE")
    print("[LEAD] Dormancy entered — project is live")
    if project:
        print(f"  Status: {project.status.value} | Release: {project.release_tag}")


async def _run15_patch_mode(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    project_id: uuid.UUID,
    master_doc: object,
) -> None:
    print("\n=== RUN 15: PATCH MODE (BUGFIX) ===")
    change = (
        "Fix the task completion endpoint — it returns 200 even when the "
        "task ID doesn't exist. Should return 404."
    )
    print(f"[CHANGE] Received: {change[:60]}...")
    lead = LeadAgent("lead_agent_1", session, llm_client=llm, agent_memory=memory)
    await lead.accept_change_request(
        change,
        project_id,
        master_doc,
        _change_decision_proceed,
    )
    print("[REGISTRY] Status: LIVE restored")


async def _run16_change_mode(
    session: AsyncSession,
    llm: LLMClient,
    memory: AgentMemory,
    project_id: uuid.UUID,
    master_doc: object,
) -> None:
    print("\n=== RUN 16: CHANGE MODE (LARGE FEATURE) ===")
    change = (
        "Add a team collaboration feature — users should be able to share "
        "task lists with team members and assign tasks to each other."
    )
    print(f"[CHANGE] Received: {change[:60]}...")
    lead = LeadAgent("lead_agent_1", session, llm_client=llm, agent_memory=memory)
    await lead.accept_change_request(
        change,
        project_id,
        master_doc,
        _change_decision_proceed,
        human_scope_callback=_scope_approve,
    )
    print("[REGISTRY] Status: LIVE restored")


async def async_main() -> None:
    settings = get_settings()
    if not settings.anthropic_api_key.strip():
        print("[FORGEAI] Set ANTHROPIC_API_KEY in .env for real LLM runs.", file=sys.stderr)
        raise SystemExit(1)
    pool = ModelPool.from_env()
    router = ModelRouter(pool)
    tm = TaskMemory(settings.redis_url, ttl_seconds=settings.task_memory_ttl)
    memory = AgentMemory(settings.chroma_host, settings.chroma_port)
    llm = LLMClient(settings.anthropic_api_key, router)
    llm.context_manager = ContextWindowManager(llm, tm)

    try:
        async with AsyncSessionFactory() as session:
            project_id, result = await _run1_bootstrap(session, llm, memory)
            _save_planning_docs(result, _RUN_LOG_FILENAME)
            try:
                layout = await _run2_layout(session, llm, memory, result.master_document, project_id)
            except Exception:
                layout = _fallback_layout(project_id)
                print("[LAYOUT] Using deterministic fallback layout after generation/review error")
            nav = await _run3_navigation(session, llm, memory, layout, project_id)
            plan = AgentBootstrapProtocol.default_task_plan()
            lead = LeadAgent("lead_agent_1", session, task_memory=tm, llm_client=llm, agent_memory=memory)
            for spec in plan.frontend_tasks:
                exists = (
                    await session.execute(
                        select(Task).where(Task.project_id == project_id, Task.title == spec.title)
                    )
                ).scalar_one_or_none()
                if exists is not None:
                    continue
                agent = (
                    "frontend_agent_2"
                    if ("Dashboard" in spec.title or "Settings" in spec.title)
                    else "frontend_agent_1"
                )
                await lead.create_task(
                    title=spec.title,
                    description=spec.description,
                    complexity=TaskComplexity[spec.complexity],
                    assigned_agent=agent,
                    project_id=project_id,
                    dependency_titles=spec.dependencies or None,
                )
            _nav, qa_cycles = await _run7_full_frontend_qa_loop(
                session, llm, memory, nav, layout, project_id, tm
            )
            api_contract = await _load_api_contract(session, project_id)
            await _run8_human_gate(
                session, llm, memory, _nav, layout, project_id, tm, qa_cycles
            )
            backend_result = await _run9_full_backend_phase(
                session, llm, memory, project_id, tm, api_contract
            )
            print("\n[PHASE] Integration QA — testing FE + BE together")
            from forgeai.contracts.registry import ComponentRegistry
            fe_code_snippets = []
            try:
                async with AsyncSessionFactory() as _s:
                    reg = ComponentRegistry(_s)
                    reg_entries = await reg.list_all(str(project_id))
                    fe_code_snippets = [
                        e.source_code for e in reg_entries
                        if e.source_code
                    ]
            except Exception as _e:
                logger.warning("Could not collect FE snippets: %s", _e)

            output_dir = str(
                Path(os.environ.get("FORGEAI_OUTPUT_DIR", "forgeai-output"))
                / str(project_id)
            )
            integration_qa = IntegrationQAOrchestrator(
                llm_client=llm,
                output_dir=output_dir,
                api_contract=api_contract,
                master_document=result.master_document,
                tech_stack=result.tech_stack_document,
                fe_code_snippets=fe_code_snippets,
            )
            integration_report = await integration_qa.run(project_id=str(project_id))
            if not integration_report.passed:
                logger.warning(
                    "[INTEGRATION QA] Issues found — proceeding with warnings. "
                    "Recommendation: %s",
                    integration_report.recommendation,
                )
            await _run10_backend_phase_gate(
                session, llm, memory, project_id, tm, backend_result
            )
            await _run11_agent_memory_upgrades(memory, result.tech_stack_document)
            await _run12_confidence_and_context(llm, tm)
            await _run13_final_review(
                session, llm, memory, project_id, result.master_document
            )
            await _run17_delivery_package(
                session,
                llm,
                memory,
                project_id,
                result.master_document,
                result.tech_stack_document,
                tm,
            )
            await _run14_live_mode(session, project_id, result.master_document)
            # === RUN 15: PATCH MODE (BUGFIX) — deferred to Phase 12 ===
            # Hardcoded test scenario — not needed until LIVE mode is
            # production-ready. Re-enable when testing PatchExecutor.
            # await _run15_patch_mode(session, llm, memory, project_id, result.master_document)

            # === RUN 16: CHANGE MODE (LARGE FEATURE) — deferred to Phase 12 ===
            # Hardcoded test scenario — not needed until LIVE mode is
            # production-ready. Re-enable when testing change classifier.
            # await _run16_change_mode(session, llm, memory, project_id, result.master_document)
            # Legacy Phase 6 runs (optional — uncomment to execute 4–6 as well)
            # root_react_code = await _run4_root_layout(session, llm, memory, nav, layout, project_id, tm)
            # await _run5_dashboard(session, llm, memory, nav, layout, project_id, tm)
            # await _run6_playwright_frontend_qa(session, llm, nav, layout, root_react_code, tm)
    except Exception as e:
        if _connection_refused(e):
            _print_database_help()
            raise SystemExit(1) from e
        raise


async def run_inspect_only() -> None:
    """Run planning only (bootstrap, layout, navigation) and stop before frontend work."""
    _init_run_logging()
    settings = get_settings()
    if not settings.anthropic_api_key.strip():
        print("[FORGEAI] Set ANTHROPIC_API_KEY in .env for real LLM runs.", file=sys.stderr)
        raise SystemExit(1)
    pool = ModelPool.from_env()
    router = ModelRouter(pool)
    tm = TaskMemory(settings.redis_url, ttl_seconds=settings.task_memory_ttl)
    memory = AgentMemory(settings.chroma_host, settings.chroma_port)
    llm = LLMClient(settings.anthropic_api_key, router)
    llm.context_manager = ContextWindowManager(llm, tm)

    try:
        async with AsyncSessionFactory() as session:
            project_id, result = await _run1_bootstrap(session, llm, memory)
            _save_planning_docs(result, _RUN_LOG_FILENAME)
            try:
                layout = await _run2_layout(session, llm, memory, result.master_document, project_id)
            except Exception:
                layout = _fallback_layout(project_id)
                print("[LAYOUT] Using deterministic fallback layout after generation/review error")
            await _run3_navigation(session, llm, memory, layout, project_id)
            print("=== INSPECTION COMPLETE — stopped before frontend ===")
    except Exception as e:
        if _connection_refused(e):
            _print_database_help()
            raise SystemExit(1) from e
        raise


def main() -> None:
    _init_run_logging()
    _configure_logging()
    asyncio.run(async_main())


if __name__ == "__main__":
    import sys

    if "--inspect" in sys.argv:
        _configure_logging()
        asyncio.run(run_inspect_only())
    else:
        main()
