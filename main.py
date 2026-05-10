"""Phase 5 demo: research + architecture with real LLM, then backend task + sandbox QA."""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.architect_agent import ArchitectAgent
from forgeai.agents.backend_agent import BackendAgent
from forgeai.agents.lead_agent import LeadAgent
from forgeai.agents.qa_agent import QAAgent
from forgeai.agents.research_agent import ResearchAgent
from forgeai.config import get_settings
from forgeai.database import AsyncSessionFactory
from forgeai.llm.client import LLMClient
from forgeai.llm.model_router import ModelRouter
from forgeai.llm.schemas import ModelPool
from forgeai.memory.agent_memory import AgentMemory
from forgeai.memory.task_memory import TaskMemory
from forgeai.models.task import TaskComplexity
from forgeai.sandbox.runner import TestRunner
from forgeai.sandbox.sandbox import Sandbox, SandboxConfig
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.transitions import KEY_WORK_OUTPUT

PROJECT_BRIEF = (
    "Build a restaurant booking website. Users should be able to "
    "browse the menu, make reservations, and manage their bookings. "
    "We need an admin panel for the restaurant owner."
)

PREFLIGHT_CONSTRAINTS = {
    "preferred_language": "Python",
    "database": "PostgreSQL",
    "deployment": "Docker",
}

_MENU_LISTING_TESTS = """
from main import list_menu_items

def test_list_menu_items_returns_list():
    items = list_menu_items()
    assert isinstance(items, list)

def test_each_item_has_name():
    for item in list_menu_items():
        assert isinstance(item, dict)
        assert "name" in item
"""


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


def _master_context_snippet(master_document, max_chars: int = 14000) -> str:
    raw = master_document.model_dump_json()
    return raw if len(raw) <= max_chars else raw[:max_chars] + "\n…(truncated)"


async def _run_research_and_architecture(session: AsyncSession) -> tuple[uuid.UUID, object, object]:
    settings = get_settings()
    if not settings.anthropic_api_key.strip():
        print(
            "[FORGEAI] Set ANTHROPIC_API_KEY in .env for real LLM runs.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    pool = ModelPool.from_env()
    router = ModelRouter(pool)
    llm = LLMClient(settings.anthropic_api_key, router)

    memory = AgentMemory(settings.chroma_host, settings.chroma_port)
    research = ResearchAgent("research_agent_1", session, llm, memory)
    architect = ArchitectAgent("architect_agent_1", session, llm, memory)
    lead = LeadAgent("lead_agent_1", session)

    print("=== RUN 1: RESEARCH AND ARCHITECTURE ===")
    ro = await research.research(PROJECT_BRIEF, PREFLIGHT_CONSTRAINTS)
    print(f"  Domain: {ro.domain_summary[:120]}{'…' if len(ro.domain_summary) > 120 else ''}")
    rs = ro.recommended_stack
    print(f"  Recommended stack: {rs.language} · {rs.framework} · {rs.database} · {rs.testing_framework}")
    print(f"  Options evaluated: {len(ro.technology_options)}")
    print(f"  Sources: {len(ro.research_sources)} references")

    md = await architect.produce_master_document(PROJECT_BRIEF, ro, PREFLIGHT_CONSTRAINTS)
    tsd = await architect.produce_tech_stack_document(ro)
    print(f"  Project: {md.project_name}")
    print(f"  Components: {len(md.components)} defined")
    print(f"  APIs: {len(md.api_surfaces)} endpoints specified")
    print(f"  Data models: {len(md.data_models)} models defined")

    project_id = uuid.uuid4()
    print("\n[LEAD] Writing to Project_Memory...")
    mid, tid = await lead.persist_master_and_tech_stack_documents(
        project_id,
        md,
        tsd,
        created_by=lead.agent_id,
    )
    print(f"[LEAD] Master_Document saved — artefact id {mid}")
    print(f"[LEAD] Tech_Stack_Document saved — artefact id {tid}")
    print()
    return project_id, md, llm


async def _run_backend_task_flow(
    session: AsyncSession,
    project_id: uuid.UUID,
    master_document,
    llm: LLMClient,
) -> None:
    settings = get_settings()
    print("=== RUN 2: BACKEND TASK WITH REAL LLM ===")
    tm = TaskMemory(settings.redis_url, ttl_seconds=settings.task_memory_ttl)
    memory = AgentMemory(settings.chroma_host, settings.chroma_port)

    lead = LeadAgent("lead_agent_1", session, task_memory=tm)
    backend = BackendAgent(
        "backend_agent_1",
        session,
        task_memory=tm,
        llm_client=llm,
        agent_memory=memory,
    )
    qa = QAAgent(
        "qa_agent_2",
        session,
        test_runner=_make_runner(TaskComplexity.MEDIUM),
        task_memory=tm,
        llm_client=llm,
    )

    task = await lead.create_task(
        title="Implement menu listing endpoint",
        description="Expose list_menu_items() returning menu dicts with a name field.",
        complexity=TaskComplexity.MEDIUM,
        assigned_agent="backend_agent_1",
        project_id=project_id,
    )
    print(f"[FORGEAI] Task created: {task.title}")
    task = await lead.approve_phase_transition(task.id)
    task = await lead.assign_task(task.id)
    print(f"[FORGEAI] Task assigned | State: {task.current_state.value}")

    master_section = _master_context_snippet(master_document)
    task = await backend.complete_work(
        task.id,
        task_description=(
            "Implement a function list_menu_items() that returns a list of dicts. "
            "Each dict must include key 'name' (str) for a menu item. "
            "Put code in main.py style — only Python source."
        ),
        master_document_section=master_section,
        loop_count=0,
    )
    print(f"[FORGEAI] Backend handed off | State: {task.current_state.value}")

    hist_machine = TaskStateMachine(session, task_memory=tm)
    hist = await hist_machine.get_history(task.id)
    code_out = ""
    for row in reversed(hist):
        meta = row.metadata_ or {}
        out = meta.get(KEY_WORK_OUTPUT)
        if isinstance(out, str) and out.strip():
            code_out = out.strip()
            break
    print(f"[FORGEAI] Generated code preview ({len(code_out.splitlines())} lines):\n{code_out[:800]}…")

    await qa.begin_review(task.id)
    print("[FORGEAI] Sandbox executing tests...")
    runner_out = await qa.review(task.id, code=code_out, test_code=_MENU_LISTING_TESTS)
    print(
        f"[FORGEAI] Tests complete: {runner_out.passed_tests}/{runner_out.total_tests} passed "
        f"(success={runner_out.success})"
    )

    if runner_out.success:
        task = await qa.approve(task.id, output="Menu listing implemented")
        print(f"[FORGEAI] QA approved | State: {task.current_state.value}")
    else:
        defect = await qa.analyze_defects(
            "Implement menu listing endpoint — list_menu_items()", runner_out
        )
        task = await qa.reject(task.id, defect_report=defect)
        print(f"[FORGEAI] QA rejected with defect report | State: {task.current_state.value}")
    print()


async def async_main() -> None:
    try:
        settings = get_settings()
        async with AsyncSessionFactory() as session:
            project_id, md, llm = await _run_research_and_architecture(session)
            await _run_backend_task_flow(session, project_id, md, llm)
    except Exception as e:
        if _connection_refused(e):
            _print_database_help()
            raise SystemExit(1) from e
        raise


def main() -> None:
    _configure_logging()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
