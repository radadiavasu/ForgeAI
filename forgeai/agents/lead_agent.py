"""Lead agent: task creation, lifecycle transitions, project artefacts."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
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
from forgeai.contracts.schemas import LayoutSpecification, NavigationContract, PageSpec, RouteDefinition
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
from forgeai.llm.schemas import APISurface, Component, MasterDocument, TechStackDocument
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
BACKEND_SERVER_JS_TASK_TITLE = "Create src/server.js"
BACKEND_DB_JS_TASK_TITLE = "Create src/db.js"
PACKAGE_JSON_TASK_TITLE = "Create package.json"
FRONTEND_INDEX_HTML_TASK_TITLE = "Create index.html"
FRONTEND_MAIN_JSX_TASK_TITLE = "Create src/main.jsx"
FRONTEND_APP_JSX_TASK_TITLE = "Create src/App.jsx with routing"
FRONTEND_VITE_CONFIG_TASK_TITLE = "Create vite.config.js"
FRONTEND_TAILWIND_CONFIG_TASK_TITLE = "Create tailwind.config.js"
BACKEND_INFRA_TASK_TITLES = (
    BACKEND_SERVER_JS_TASK_TITLE,
    BACKEND_DB_JS_TASK_TITLE,
)
FRONTEND_INFRA_TASK_TITLES = (
    FRONTEND_INDEX_HTML_TASK_TITLE,
    FRONTEND_MAIN_JSX_TASK_TITLE,
    FRONTEND_APP_JSX_TASK_TITLE,
    PACKAGE_JSON_TASK_TITLE,
    FRONTEND_VITE_CONFIG_TASK_TITLE,
    FRONTEND_TAILWIND_CONFIG_TASK_TITLE,
)
HISTORY_PAGE_TASK_TITLE = "Create History page"
API_CLIENT_TASK_TITLE = "Create API client module"
DATABASE_MIGRATION_TASK_TITLE = "Create database migration"
DOCKER_COMPOSE_TASK_TITLE = "Create Docker Compose"
BACKEND_DOCKERFILE_TASK_TITLE = "Create backend Dockerfile"
FRONTEND_DOCKERFILE_TASK_TITLE = "Create frontend Dockerfile"


def _task_title_substring_exists(existing_titles: list[str], needle: str) -> bool:
    """Return True if any existing title contains needle (case-insensitive)."""
    needle_lower = needle.lower()
    return any(needle_lower in title.lower() for title in existing_titles)


def load_skill(skill_name: str) -> str:
    """Load a skill file from the skills directory."""
    skill_path = Path(f"skills/{skill_name}/SKILL.md")
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    return ""


def select_backend_skill(tech_stack: TechStackDocument) -> str:
    """Dynamically select backend skill based on tech stack."""
    framework = tech_stack.framework.lower()
    if "express" in framework:
        return load_skill("express-server")
    if "fastapi" in framework:
        return load_skill("fastapi-server")
    if "django" in framework:
        return load_skill("django-server")
    return ""


def select_frontend_skill(tech_stack: TechStackDocument) -> str:
    """Dynamically select frontend skill based on tech stack."""
    libraries = " ".join(tech_stack.libraries).lower()
    framework = tech_stack.framework.lower()
    if "react" in libraries or "react" in framework:
        if "vite" in libraries:
            return load_skill("react-vite-app")
    if "next" in framework:
        return load_skill("nextjs-app")
    if "vue" in libraries:
        return load_skill("vue-vite-app")
    return ""


def _tech_stack_document_from_master(master_doc: MasterDocument) -> TechStackDocument:
    ts = master_doc.tech_stack
    return TechStackDocument(
        language=ts.language,
        framework=ts.framework,
        database=ts.database,
        testing_framework=ts.testing_framework,
        libraries=list(getattr(ts, "libraries", []) or []),
        rationale=ts.rationale,
        rejected_alternatives=list(getattr(ts, "rejected_alternatives", []) or []),
    )


def _backend_server_js_task_description(
    master_doc: MasterDocument,
    tech_stack: TechStackDocument,
) -> str:
    backend_skill = select_backend_skill(tech_stack)
    skill_section = f"\n\nSKILL REFERENCE:\n{backend_skill}" if backend_skill else ""
    endpoint_lines = "\n".join(
        f"  {s.method} {s.endpoint} — {s.description}"
        for s in master_doc.api_surfaces
    )
    return f"""Create ONLY the Express app entry point file: src/server.js

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}
Database: {tech_stack.database}
Libraries: {', '.join(tech_stack.libraries)}

API endpoints to wire up (reference only — do not create route files here):
{endpoint_lines}

Output a single src/server.js file with Express app setup, middleware, and /api router mount.
Do not generate db.js, package.json, or any other files.
{skill_section}
"""


def _backend_db_js_task_description(tech_stack: TechStackDocument) -> str:
    backend_skill = select_backend_skill(tech_stack)
    skill_section = f"\n\nSKILL REFERENCE:\n{backend_skill}" if backend_skill else ""
    return f"""Create ONLY the PostgreSQL connection pool file: src/db.js

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}
Database: {tech_stack.database}

Output a single src/db.js file exporting a pg Pool and query helper.
Do not generate server.js, package.json, or any other files.
{skill_section}
"""


def _package_json_task_description(tech_stack: TechStackDocument) -> str:
    backend_skill = select_backend_skill(tech_stack)
    frontend_skill = select_frontend_skill(tech_stack)
    skill_parts = []
    if backend_skill:
        skill_parts.append(f"BACKEND SKILL REFERENCE:\n{backend_skill}")
    if frontend_skill:
        skill_parts.append(f"FRONTEND SKILL REFERENCE:\n{frontend_skill}")
    skill_section = (
        f"\n\n{chr(10).join(skill_parts)}" if skill_parts else ""
    )
    return f"""Create ONLY a unified package.json at the project root.

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}
Database: {tech_stack.database}
Libraries: {', '.join(tech_stack.libraries)}

Include BOTH frontend and backend dependencies in one file:
- Frontend: React, Vite, Tailwind, react-router-dom, dev/build scripts
- Backend: Express, pg, cors, helmet, dotenv, vitest, nodemon scripts

Output a single package.json with type module, dependencies, devDependencies,
and scripts for dev, start, build, and test.
Do not generate source or config files.
{skill_section}
"""


def _frontend_index_html_task_description(tech_stack: TechStackDocument) -> str:
    frontend_skill = select_frontend_skill(tech_stack)
    skill_section = f"\n\nSKILL REFERENCE:\n{frontend_skill}" if frontend_skill else ""
    return f"""Create ONLY the Vite entry HTML file: index.html (project root).

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}
Libraries: {', '.join(tech_stack.libraries)}

Output a single index.html with root div and script tag pointing to /src/main.jsx.
Do not generate JSX, config, or any other files.
{skill_section}
"""


def _frontend_main_jsx_task_description(tech_stack: TechStackDocument) -> str:
    frontend_skill = select_frontend_skill(tech_stack)
    skill_section = f"\n\nSKILL REFERENCE:\n{frontend_skill}" if frontend_skill else ""
    return f"""Create ONLY the React root mount file: src/main.jsx

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}
Libraries: {', '.join(tech_stack.libraries)}

Output a single src/main.jsx that mounts App into #root.
Do not generate App.jsx, index.html, or any other files.
{skill_section}
"""


def _frontend_app_jsx_task_description(
    nav: NavigationContract,
    tech_stack: TechStackDocument,
) -> str:
    frontend_skill = select_frontend_skill(tech_stack)
    skill_section = f"\n\nSKILL REFERENCE:\n{frontend_skill}" if frontend_skill else ""
    route_lines = "\n".join(
        f"  {r.path} → {r.component_name}" for r in nav.routes
    )
    return f"""Create ONLY src/App.jsx with BrowserRouter and route definitions.

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}
Libraries: {', '.join(tech_stack.libraries)}

Routes to wire:
{route_lines}

Output a single src/App.jsx with Router, Routes, and Route entries for the paths above.
Do not generate main.jsx, page components, or any other files.
{skill_section}
"""


def _frontend_vite_config_task_description(tech_stack: TechStackDocument) -> str:
    frontend_skill = select_frontend_skill(tech_stack)
    skill_section = f"\n\nSKILL REFERENCE:\n{frontend_skill}" if frontend_skill else ""
    return f"""Create ONLY vite.config.js for the React + Vite project.

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}
Libraries: {', '.join(tech_stack.libraries)}

Output a single vite.config.js with @vitejs/plugin-react.
Do not generate any other files.
{skill_section}
"""


def _frontend_tailwind_config_task_description(tech_stack: TechStackDocument) -> str:
    frontend_skill = select_frontend_skill(tech_stack)
    skill_section = f"\n\nSKILL REFERENCE:\n{frontend_skill}" if frontend_skill else ""
    return f"""Create ONLY tailwind.config.js for the React + Vite project.

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}
Libraries: {', '.join(tech_stack.libraries)}

Output a single tailwind.config.js with content paths for index.html and src/**/*.
Do not generate postcss.config.js or any other files.
{skill_section}
"""


def _backend_endpoint_task_title(method: str, path: str) -> str:
    """Title for a backend task tied to one API surface."""
    verb = (method or "GET").strip().upper()
    route = (path or "/").strip()
    if route and not route.startswith("/"):
        route = f"/{route}"
    return f"Implement {verb} {route}"


def _backend_tasks_from_master_doc(
    master_doc: MasterDocument,
    tech_stack: TechStackDocument | None = None,
) -> list[TaskSpec]:
    """Backend infrastructure files first, then one task per API surface."""
    if not master_doc.api_surfaces:
        return []

    tasks: list[TaskSpec] = []
    if tech_stack is not None:
        tasks.extend(
            [
                TaskSpec(
                    title=BACKEND_SERVER_JS_TASK_TITLE,
                    description=_backend_server_js_task_description(
                        master_doc, tech_stack
                    ).strip(),
                    complexity="MEDIUM",
                    phase="BACKEND_PHASE",
                    dependencies=[],
                ),
                TaskSpec(
                    title=BACKEND_DB_JS_TASK_TITLE,
                    description=_backend_db_js_task_description(tech_stack).strip(),
                    complexity="MEDIUM",
                    phase="BACKEND_PHASE",
                    dependencies=[],
                ),
            ]
        )
    endpoint_deps = list(BACKEND_INFRA_TASK_TITLES) if tech_stack is not None else []
    for surface in master_doc.api_surfaces:
        title = f"Implement {surface.method} {surface.endpoint}"
        desc = (surface.description or "").strip() or title
        tasks.append(
            TaskSpec(
                title=title,
                description=desc,
                complexity="MEDIUM",
                phase="BACKEND_PHASE",
                dependencies=list(endpoint_deps),
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


def _frontend_tasks_from_navigation(
    nav: NavigationContract,
    tech_stack: TechStackDocument | None = None,
) -> list[TaskSpec]:
    """Frontend tasks: infrastructure files first, then layout, then page components."""
    tasks: list[TaskSpec] = []
    shell_deps: list[str] = []

    if tech_stack is not None:
        tasks.extend(
            [
                TaskSpec(
                    title=FRONTEND_INDEX_HTML_TASK_TITLE,
                    description=_frontend_index_html_task_description(
                        tech_stack
                    ).strip(),
                    complexity="MEDIUM",
                    phase="FRONTEND_PHASE",
                    dependencies=[],
                ),
                TaskSpec(
                    title=FRONTEND_MAIN_JSX_TASK_TITLE,
                    description=_frontend_main_jsx_task_description(
                        tech_stack
                    ).strip(),
                    complexity="MEDIUM",
                    phase="FRONTEND_PHASE",
                    dependencies=[],
                ),
                TaskSpec(
                    title=FRONTEND_APP_JSX_TASK_TITLE,
                    description=_frontend_app_jsx_task_description(
                        nav, tech_stack
                    ).strip(),
                    complexity="MEDIUM",
                    phase="FRONTEND_PHASE",
                    dependencies=[],
                ),
                TaskSpec(
                    title=PACKAGE_JSON_TASK_TITLE,
                    description=_package_json_task_description(tech_stack).strip(),
                    complexity="MEDIUM",
                    phase="FRONTEND_PHASE",
                    dependencies=[],
                ),
                TaskSpec(
                    title=FRONTEND_VITE_CONFIG_TASK_TITLE,
                    description=_frontend_vite_config_task_description(
                        tech_stack
                    ).strip(),
                    complexity="MEDIUM",
                    phase="FRONTEND_PHASE",
                    dependencies=[],
                ),
                TaskSpec(
                    title=FRONTEND_TAILWIND_CONFIG_TASK_TITLE,
                    description=_frontend_tailwind_config_task_description(
                        tech_stack
                    ).strip(),
                    complexity="MEDIUM",
                    phase="FRONTEND_PHASE",
                    dependencies=[],
                ),
            ]
        )
        shell_deps = list(FRONTEND_INFRA_TASK_TITLES)

    tasks.append(
        TaskSpec(
            title=ROOT_LAYOUT_TASK_TITLE,
            description="Root layout and shared navigation shell.",
            complexity="MEDIUM",
            phase="FRONTEND_PHASE",
            dependencies=list(shell_deps),
        )
    )
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


def _history_page_task_description(
    master_document: MasterDocument,
    tech_stack: TechStackDocument,
    history_route: RouteDefinition,
    history_endpoint: APISurface,
) -> str:
    frontend_criteria = chr(10).join(
        line
        for c in master_document.components
        if "frontend" in c.name.lower()
        for line in c.acceptance_criteria
    )
    return f"""Build the {history_route.component_name}
component showing completed tasks.

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}

Fetch data from: {history_endpoint.method} {history_endpoint.endpoint}
Response schema: {history_endpoint.response_schema}

Requirements from Master Document:
{frontend_criteria}"""


def _api_client_task_description(
    master_document: MasterDocument,
    tech_stack: TechStackDocument,
) -> str:
    frontend_skill = select_frontend_skill(tech_stack)
    endpoint_lines = chr(10).join(
        f"  {s.method} {s.endpoint} — {s.description}"
        for s in master_document.api_surfaces
    )
    return f"""Create a centralised API client module
that wraps all backend endpoint calls.

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}

Endpoints to wrap:
{endpoint_lines}

Requirements:
- One exported function per endpoint
- Handle loading and error states
- Read API base URL from environment
- No hardcoded URLs

{frontend_skill}"""


def _database_migration_task_description(
    master_document: MasterDocument,
    tech_stack: TechStackDocument,
    db_component: Component,
) -> str:
    model_lines = chr(10).join(
        f"  {m.name}: "
        + ", ".join(f"{f.name}({f.type})" for f in m.fields)
        for m in master_document.data_models
    )
    criteria_lines = chr(10).join(db_component.acceptance_criteria)
    backend_skill = select_backend_skill(tech_stack)
    return f"""Create database migration scripts for
{tech_stack.database}.

Data models to implement:
{model_lines}

Acceptance criteria:
{criteria_lines}

{backend_skill}"""


def _docker_compose_task_description(
    master_document: MasterDocument,
    tech_stack: TechStackDocument,
    docker_component: Component,
) -> str:
    component_lines = chr(10).join(
        f"  {c.name}" for c in master_document.components
    )
    criteria_lines = chr(10).join(docker_component.acceptance_criteria)
    return f"""Create Docker Compose configuration
wiring all services.

Components to orchestrate:
{component_lines}

Database: {tech_stack.database}

Acceptance criteria:
{criteria_lines}"""


def _backend_dockerfile_task_description(tech_stack: TechStackDocument) -> str:
    backend_skill = select_backend_skill(tech_stack)
    return f"""Create Dockerfile for the backend service.

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}

Requirements:
- Multi-stage build
- Minimal production image
- Read configuration from environment variables only
- No hardcoded secrets or ports

{backend_skill}"""


def _frontend_dockerfile_task_description(tech_stack: TechStackDocument) -> str:
    frontend_skill = select_frontend_skill(tech_stack)
    return f"""Create Dockerfile for the frontend service.

Tech stack: {tech_stack.language}
Framework: {tech_stack.framework}

Requirements:
- Stage 1: build the frontend application
- Stage 2: serve static files with a lightweight web server
- No hardcoded ports
- Read configuration from environment variables

{frontend_skill}"""


def _missing_tasks_from_documents(
    master_document: MasterDocument,
    tech_stack: TechStackDocument,
    navigation_contract: NavigationContract | None,
    existing_titles: list[str],
) -> list[TaskSpec]:
    """Build supplemental tasks from Master Document artefacts after decomposition."""
    tasks: list[TaskSpec] = []
    titles = list(existing_titles)

    if navigation_contract is not None:
        history_route = next(
            (
                r
                for r in navigation_contract.routes
                if "history" in r.path.lower()
            ),
            None,
        )
        if (
            history_route is not None
            and not _task_title_substring_exists(titles, "history")
            and master_document.api_surfaces
        ):
            history_endpoint = next(
                (
                    s
                    for s in master_document.api_surfaces
                    if "history" in s.endpoint.lower()
                    or (
                        "complete" in s.endpoint.lower()
                        and s.method.upper() == "GET"
                    )
                ),
                master_document.api_surfaces[0],
            )
            tasks.append(
                TaskSpec(
                    title=HISTORY_PAGE_TASK_TITLE,
                    description=_history_page_task_description(
                        master_document,
                        tech_stack,
                        history_route,
                        history_endpoint,
                    ).strip(),
                    complexity="MEDIUM",
                    phase="FRONTEND_PHASE",
                    dependencies=[ROOT_LAYOUT_TASK_TITLE],
                )
            )
            titles.append(HISTORY_PAGE_TASK_TITLE)

        if not _task_title_substring_exists(titles, "api client"):
            tasks.append(
                TaskSpec(
                    title=API_CLIENT_TASK_TITLE,
                    description=_api_client_task_description(
                        master_document, tech_stack
                    ).strip(),
                    complexity="MEDIUM",
                    phase="FRONTEND_PHASE",
                    dependencies=[ROOT_LAYOUT_TASK_TITLE],
                )
            )
            titles.append(API_CLIENT_TASK_TITLE)

    db_component = next(
        (c for c in master_document.components if "database" in c.name.lower()),
        None,
    )
    if db_component is not None and not _task_title_substring_exists(
        titles, "migration"
    ):
        backend_deps = (
            list(BACKEND_INFRA_TASK_TITLES)
            if any(t in titles for t in BACKEND_INFRA_TASK_TITLES)
            else []
        )
        tasks.append(
            TaskSpec(
                title=DATABASE_MIGRATION_TASK_TITLE,
                description=_database_migration_task_description(
                    master_document, tech_stack, db_component
                ).strip(),
                complexity="HIGH",
                phase="BACKEND_PHASE",
                dependencies=backend_deps,
            )
        )
        titles.append(DATABASE_MIGRATION_TASK_TITLE)

    docker_component = next(
        (
            c
            for c in master_document.components
            if "docker" in c.name.lower() or "compose" in c.name.lower()
        ),
        None,
    )
    if docker_component is not None and not _task_title_substring_exists(
        titles, "docker compose"
    ):
        tasks.append(
            TaskSpec(
                title=DOCKER_COMPOSE_TASK_TITLE,
                description=_docker_compose_task_description(
                    master_document, tech_stack, docker_component
                ).strip(),
                complexity="MEDIUM",
                phase="BACKEND_PHASE",
                dependencies=[],
            )
        )
        titles.append(DOCKER_COMPOSE_TASK_TITLE)

    if not _task_title_substring_exists(titles, "backend dockerfile"):
        tasks.append(
            TaskSpec(
                title=BACKEND_DOCKERFILE_TASK_TITLE,
                description=_backend_dockerfile_task_description(
                    tech_stack
                ).strip(),
                complexity="MEDIUM",
                phase="BACKEND_PHASE",
                dependencies=[],
            )
        )
        titles.append(BACKEND_DOCKERFILE_TASK_TITLE)

    if not _task_title_substring_exists(titles, "frontend dockerfile"):
        tasks.append(
            TaskSpec(
                title=FRONTEND_DOCKERFILE_TASK_TITLE,
                description=_frontend_dockerfile_task_description(
                    tech_stack
                ).strip(),
                complexity="MEDIUM",
                phase="BACKEND_PHASE",
                dependencies=[],
            )
        )
        titles.append(FRONTEND_DOCKERFILE_TASK_TITLE)

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
            tech_doc = _tech_stack_document_from_master(master_doc)
            backend_tasks = _backend_tasks_from_master_doc(master_doc, tech_doc)
        elif llm_plan is not None and llm_plan.backend_tasks:
            backend_tasks = llm_plan.backend_tasks
        elif default_plan.backend_tasks:
            backend_tasks = list(default_plan.backend_tasks)
        else:
            backend_tasks = _numbered_backend_tasks(1)

        if use_nav_frontend:
            tech_doc = _tech_stack_document_from_master(master_doc)
            frontend_tasks = _frontend_tasks_from_navigation(navigation_contract, tech_doc)
        elif llm_plan is not None and llm_plan.frontend_tasks:
            frontend_tasks = llm_plan.frontend_tasks
        else:
            frontend_tasks = list(default_plan.frontend_tasks)

        tech_doc = _tech_stack_document_from_master(master_doc)
        plan_titles = [t.title for t in frontend_tasks + backend_tasks]
        for spec in _missing_tasks_from_documents(
            master_doc, tech_doc, navigation_contract, plan_titles
        ):
            if spec.phase == "FRONTEND_PHASE":
                frontend_tasks.append(spec)
            else:
                backend_tasks.append(spec)
            plan_titles.append(spec.title)

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

    async def _load_tech_stack_document_for_project(
        self, project_id: uuid.UUID
    ) -> TechStackDocument | None:
        row = (
            await self.db.execute(
                select(ProjectArtefactModel).where(
                    ProjectArtefactModel.project_id == project_id,
                    ProjectArtefactModel.artefact_type == "tech_stack_document",
                    ProjectArtefactModel.is_current.is_(True),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return TechStackDocument.model_validate(row.content)

    async def create_missing_tasks_after_decomposition(
        self,
        project_id: uuid.UUID,
        master_document: MasterDocument,
        *,
        navigation_contract: NavigationContract | None = None,
        tech_stack: TechStackDocument | None = None,
        frontend_agent: str = "frontend_agent_1",
        backend_agent: str = "backend_agent_1",
    ) -> list[Task]:
        """Create supplemental tasks from Master Document after primary decomposition."""
        if tech_stack is None:
            tech_stack = await self._load_tech_stack_document_for_project(project_id)
            if tech_stack is None:
                tech_stack = _tech_stack_document_from_master(master_document)

        res = await self.db.execute(
            select(Task.title).where(Task.project_id == project_id)
        )
        existing_titles = [row[0] for row in res.all()]

        specs = _missing_tasks_from_documents(
            master_document,
            tech_stack,
            navigation_contract,
            existing_titles,
        )

        created: list[Task] = []
        for spec in specs:
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
            agent = (
                frontend_agent if spec.phase == "FRONTEND_PHASE" else backend_agent
            )
            task = await self.create_task(
                title=spec.title,
                description=spec.description,
                complexity=TaskComplexity[spec.complexity],
                assigned_agent=agent,
                project_id=project_id,
                dependency_titles=spec.dependencies or None,
            )
            created.append(task)
            existing_titles.append(spec.title)
        return created

    async def create_backend_tasks_from_master_document(
        self,
        project_id: uuid.UUID,
        master_document: MasterDocument,
        *,
        navigation_contract: NavigationContract | None = None,
        assigned_agent: str = "backend_agent_1",
    ) -> list[Task]:
        """Create backend server entry point then one task per API surface."""
        tech_stack = await self._load_tech_stack_document_for_project(project_id)
        if tech_stack is None:
            tech_stack = _tech_stack_document_from_master(master_document)

        created: list[Task] = []
        if master_document.api_surfaces:
            for spec in _backend_tasks_from_master_doc(master_document, tech_stack):
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
                    dependency_titles=spec.dependencies or None,
                )
                created.append(task)
        created.extend(
            await self.create_missing_tasks_after_decomposition(
                project_id,
                master_document,
                navigation_contract=navigation_contract,
                tech_stack=tech_stack,
                backend_agent=assigned_agent,
            )
        )
        return created

    async def create_frontend_tasks_from_navigation(
        self,
        project_id: uuid.UUID,
        navigation_contract: NavigationContract,
        tech_stack: TechStackDocument | None = None,
        *,
        assigned_agent: str = "frontend_agent_1",
    ) -> list[Task]:
        """Create frontend app shell, layout, and page tasks from navigation contract."""
        if tech_stack is None:
            tech_stack = await self._load_tech_stack_document_for_project(project_id)

        created: list[Task] = []
        for spec in _frontend_tasks_from_navigation(navigation_contract, tech_stack):
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
            agent = assigned_agent
            if (
                spec.title not in FRONTEND_INFRA_TASK_TITLES
                and spec.title != ROOT_LAYOUT_TASK_TITLE
            ):
                if "Dashboard" in spec.title or "Settings" in spec.title:
                    agent = "frontend_agent_2"
            task = await self.create_task(
                title=spec.title,
                description=spec.description,
                complexity=TaskComplexity[spec.complexity],
                assigned_agent=agent,
                project_id=project_id,
                dependency_titles=spec.dependencies or None,
            )
            created.append(task)
        master = await self._load_master_document_for_project(project_id)
        if master is not None:
            created.extend(
                await self.create_missing_tasks_after_decomposition(
                    project_id,
                    master,
                    navigation_contract=navigation_contract,
                    tech_stack=tech_stack,
                    frontend_agent=assigned_agent,
                )
            )
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
        root_title = ROOT_LAYOUT_TASK_TITLE
        await self.create_frontend_tasks_from_navigation(
            project_id, navigation_contract
        )
        res = await self.db.execute(
            select(Task).where(Task.project_id == project_id)
        )
        all_tasks = list(res.scalars())
        infra_titles = set(FRONTEND_INFRA_TASK_TITLES)
        frontend_tasks = [
            t
            for t in all_tasks
            if t.title in infra_titles
            or t.title == root_title
            or "page" in t.title.lower()
            or "component" in t.title.lower()
            or "AppLayout" in t.title
            or "api client" in t.title.lower()
        ]
        shell_tasks = [t for t in frontend_tasks if t.title in infra_titles]
        root_tasks = [t for t in frontend_tasks if t.title == root_title]
        other_tasks = [
            t
            for t in frontend_tasks
            if t.title not in infra_titles and t.title != root_title
        ]

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

        for shell in shell_tasks:
            page = next((p for p in layout_spec.pages if p.route == "/"), layout_spec.pages[0])
            await _run_task_cycle(shell, page)
            await self.unlock_dependent_tasks(shell.title, project_id)

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
                    project_id, master, navigation_contract=navigation_contract
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
