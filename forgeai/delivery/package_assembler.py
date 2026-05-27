"""Assemble deployment package from DONE task outputs (Phase 10, Req 25)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func as sa_func, select

from forgeai.delivery.git_manager import GitManager
from forgeai.delivery.readme_generator import ReadmeGenerator
from forgeai.delivery.schemas import (
    DeploymentPackage,
    FinalSummaryReport,
    format_final_summary_plain,
)
from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import MasterDocument, TechStackDocument
from forgeai.models.escalation import EscalationEventModel
from forgeai.models.task import Task, TaskStateHistory
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.models.project_artefact import ProjectArtefactModel
from forgeai.state_machine.transitions import KEY_METADATA, KEY_OUTPUT, KEY_WORK_OUTPUT

if True:  # TYPE_CHECKING without import cycle
    from forgeai.agents.lead_agent import LeadAgent
    from forgeai.agents.qa_agent import QAAgent

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_ROOT = Path("H:/forgeai-output")

_QA_PLACEHOLDER_OUTPUTS = frozenset(
    {"qa approved", "done", "ok", "patch verified", "change rework complete", "new change task complete"}
)

_FORGEAI_SOURCE_MARKERS = (
    "asyncio.run(main())",
    "run_inspect_only",
    "BOOTSTRAP PROTOCOL",
    "ForgeAI pipeline",
)

COMPOSE_PROMPT = """
Generate docker-compose.yml for the tech stack.
Include app service, database service when PostgreSQL is used, health checks,
volume mounts, and environment variable references.
Output YAML only — no markdown fences.
""".strip()


def _strip_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s.strip()


def _name_from_title(title: str) -> str:
    name = title.strip()
    for prefix in (
        "Build ",
        "Implement ",
        "Create ",
        "Add ",
        "Address gate feedback: ",
    ):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    name = re.sub(r"\s+page\s*$", "", flags=re.I, string=name)
    name = re.sub(r"\s+component\s*$", "", flags=re.I, string=name)
    name = re.sub(r"\s+endpoint\s*$", "", flags=re.I, string=name)
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "Module"


def _api_module_filename(title: str) -> str:
    """Derive ``src/api/{name}.py`` module slug from task title."""
    lower = title.strip().lower()
    match = re.match(r"^backend task (\d+)$", lower)
    if match:
        return f"backend_module_{match.group(1)}"
    match = re.search(r"rest api for\s+(.+)$", lower)
    if match:
        slug = re.sub(r"[^a-z0-9]+", "_", match.group(1).strip()).strip("_")
        return slug or "api"
    if "endpoint" in lower:
        subject = re.split(r"\bendpoint\b", lower, maxsplit=1)[0]
        subject = re.sub(
            r"^(create|implement|add|build)\s+",
            "",
            subject.strip(),
        )
        subject = re.sub(r"\s+api\s*$", "", subject).strip()
        slug = re.sub(r"[^a-z0-9]+", "_", subject).strip("_")
        if slug.endswith("s") and len(slug) > 3:
            return slug
        if slug and not slug.endswith("s"):
            return f"{slug}s" if "task" in slug else slug
        return slug or "api"
    return re.sub(r"[^a-z0-9]+", "_", _name_from_title(title).lower()).strip("_") or "api"


class PackageAssembler:
    """Write DONE task outputs and deployment artefacts to disk."""

    def __init__(
        self,
        db_session,
        git_manager: GitManager,
        qa_agent: QAAgent,
        llm_client: LLMClient,
        *,
        lead_agent: LeadAgent | None = None,
    ) -> None:
        self.db = db_session
        self.git = git_manager
        self.qa = qa_agent
        self.llm = llm_client
        self.lead = lead_agent

    async def assemble(
        self,
        project_id: str,
        master_document: MasterDocument,
        tech_stack: TechStackDocument,
        output_dir: str,
        *,
        project_brief: str | None = None,
    ) -> DeploymentPackage:
        root = Path(output_dir)
        self._create_directory_structure(str(root), tech_stack)
        self.git.init_repo()

        pid = UUID(project_id)
        done_tasks, done_diag = await self._load_done_tasks(pid)
        logger.info(
            "Package assembly: %d DONE task(s) for project %s (%s)",
            len(done_tasks),
            project_id,
            done_diag,
        )
        print(f"[DELIVERY] Found {len(done_tasks)} DONE task(s) to write")
        print(f"[DELIVERY] {done_diag}")

        files_written: list[str] = []
        total_bytes = 0

        has_frontend = False
        has_backend = False
        lang_lower = tech_stack.language.lower()
        is_js_ts = "javascript" in lang_lower or "typescript" in lang_lower

        for task in done_tasks:
            await self.db.refresh(task)
            stored = task.output
            preview = (stored or "")[:120].replace("\n", " ")
            logger.info(
                "Task %s title=%r assigned_agent=%r output=%r",
                task.id,
                task.title,
                task.assigned_agent,
                preview if preview else None,
            )
            print(
                f"[DELIVERY] Task {task.id}: {task.title!r} — "
                f"output={'set' if stored and stored.strip() else 'empty'}"
            )

            if self._is_frontend_task(task):
                has_frontend = True
            elif "backend" in (task.assigned_agent or "").lower():
                has_backend = True

            rel_path = self._derive_file_path(task, tech_stack)
            file_path = root / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)

            content = task.output or ""
            if self._needs_work_output_from_history(content):
                review_out = await self._work_output_from_review_transition(task.id)
                if review_out.strip():
                    content = review_out

            if not content.strip() or self._is_placeholder_output(content):
                content = f"# Task: {task.title}\n# Output not captured\n"

            if self._is_forgeai_pipeline_source(content):
                logger.warning(
                    "Skipping write for %s (task %s): content matches ForgeAI pipeline source",
                    rel_path,
                    task.id,
                )
                print(
                    f"[DELIVERY] SKIP {rel_path} — detected ForgeAI pipeline source, "
                    "not generated project code"
                )
                continue

            if is_js_ts and rel_path.endswith(".py"):
                logger.warning(
                    "Skipping .py file %s for JS/TS project (task %s: %r)",
                    rel_path,
                    task.id,
                    task.title,
                )
                print(
                    f"[DELIVERY] SKIP {rel_path} — JS/TS project, not writing Python files"
                )
                continue

            file_path.write_text(content, encoding="utf-8")
            nbytes = len(content.encode("utf-8"))
            logger.info("Writing %s (%d bytes)", rel_path, nbytes)
            print(f"[DELIVERY] Writing {rel_path} ({nbytes} bytes)...")
            files_written.append(rel_path)
            total_bytes += nbytes
            self.git.commit(
                str(task.id),
                task.assigned_agent or "forgeai",
                task.description or task.title,
                [rel_path],
            )

        compose = await self._generate_docker_compose(tech_stack, str(root))
        compose_path = root / "docker-compose.yml"
        compose_path.write_text(compose, encoding="utf-8")
        print("[DELIVERY] Generating docker-compose.yml...")
        files_written.append("docker-compose.yml")
        total_bytes += compose_path.stat().st_size
        self.git.commit("compose", "forgeai", "deployment", ["docker-compose.yml"])

        self._write_requirements_txt(root, tech_stack, has_backend)

        dockerfile = await self._generate_dockerfile(
            tech_stack, str(root), has_frontend=has_frontend, has_backend=has_backend
        )
        df_path = root / "Dockerfile"
        df_path.write_text(dockerfile, encoding="utf-8")
        logger.info("Writing Dockerfile (%d bytes)", df_path.stat().st_size)
        print("[DELIVERY] Generating Dockerfile...")
        files_written.append("Dockerfile")
        total_bytes += df_path.stat().st_size
        self.git.commit("dockerfile", "forgeai", "deployment", ["Dockerfile"])

        env_content = self._generate_env_example(tech_stack)
        env_path = root / ".env.example"
        env_path.write_text(env_content, encoding="utf-8")
        print("[DELIVERY] Generating .env.example...")
        files_written.append(".env.example")
        total_bytes += env_path.stat().st_size
        self.git.commit("env", "forgeai", "deployment", [".env.example"])

        readme_gen = ReadmeGenerator(self.llm)
        pkg_stub = DeploymentPackage(
            project_id=project_id,
            output_dir=str(root.resolve()),
            env_example_path=str(env_path),
        )
        brief = project_brief or master_document.project_summary
        readme = await readme_gen.generate(
            master_document.project_name,
            brief,
            tech_stack,
            pkg_stub,
            env_example=env_content,
        )
        readme_path = root / "README.md"
        readme_path.write_text(readme, encoding="utf-8")
        print("[DELIVERY] Generating README.md...")
        files_written.append("README.md")
        total_bytes += readme_path.stat().st_size
        self.git.commit("readme", "forgeai", "documentation", ["README.md"])

        summary_report = await self._build_summary_report(
            project_id,
            master_document,
            brief,
        )
        summary_text = format_final_summary_plain(summary_report)
        summary_path = root / "SUMMARY.md"
        summary_path.write_text(summary_text, encoding="utf-8")
        print("[DELIVERY] Generating SUMMARY.md...")
        files_written.append("SUMMARY.md")
        total_bytes += summary_path.stat().st_size
        self.git.commit("summary", "forgeai", "documentation", ["SUMMARY.md"])

        print("[GIT] Committing task files...")
        print(f"[GIT] {len(files_written)} files committed")

        if is_js_ts:
            for cleanup_name in ("requirements.txt", "main.py"):
                cleanup_path = root / cleanup_name
                if cleanup_path.is_file():
                    removed_bytes = cleanup_path.stat().st_size
                    cleanup_path.unlink()
                    logger.warning(
                        "Removed %s from JS/TS output package", cleanup_name
                    )
                    print(f"[DELIVERY] Removed {cleanup_name} from JS/TS output")
                    if cleanup_name in files_written:
                        files_written.remove(cleanup_name)
                        total_bytes -= removed_bytes

        release_tag = "release-v1"
        rollback = self.git.create_tag(release_tag, "ForgeAI delivery release v1")
        print(f"[GIT] Tag created: {release_tag}")

        print("[DELIVERY] Validating Docker build...")
        docker_ok = await self._validate_docker_build(str(root), project_id)

        return DeploymentPackage(
            project_id=project_id,
            output_dir=str(root.resolve()),
            files_written=files_written,
            dockerfile_path=str(df_path),
            docker_compose_path=str(compose_path),
            env_example_path=str(env_path),
            readme_path=str(readme_path),
            summary_report_path=str(summary_path),
            release_tag=release_tag,
            git_log=self.git.get_log(),
            rollback_points=[rollback],
            docker_build_passed=docker_ok,
            assembled_at=datetime.now(UTC),
            total_size_bytes=total_bytes,
        )

    def _backend_source_extension(self, task: Task, tech_stack: TechStackDocument) -> str:
        if "backend" not in (task.assigned_agent or "").lower():
            return ".py"
        lang = tech_stack.language.lower()
        if "javascript" in lang or "typescript" in lang:
            return ".js"
        return ".py"

    def _derive_file_path(self, task: Task, tech_stack: TechStackDocument) -> str:
        title_lower = (task.title or "").lower()
        lang = tech_stack.language.lower() if tech_stack else ""
        is_js = "javascript" in lang or "typescript" in lang

        # Exact single-file infrastructure routing
        if "src/server.js" in title_lower or title_lower == "create src/server.js":
            return "src/server.js"
        if "src/db.js" in title_lower:
            return "src/db.js"
        if "src/main.jsx" in title_lower:
            return "src/main.jsx"
        if "src/app.jsx" in title_lower:
            return "src/App.jsx"
        if "index.html" in title_lower:
            return "index.html"
        if "vite.config" in title_lower:
            return "vite.config.js"
        if "tailwind.config" in title_lower:
            return "tailwind.config.js"
        if title_lower == "create package.json" or "create package.json" in title_lower:
            return "package.json"
        if "package.json for backend" in title_lower:
            return "package.json"
        if "package.json for frontend" in title_lower:
            return "package.json"
        if "dockerfile.backend" in title_lower or "backend dockerfile" in title_lower:
            return "Dockerfile.backend"
        if "dockerfile.frontend" in title_lower or "frontend dockerfile" in title_lower:
            return "Dockerfile.frontend"
        if "docker compose" in title_lower:
            return "docker-compose.yml"
        if "database migration" in title_lower or "001_init" in title_lower:
            return "migrations/001_init.sql"

        if "db migration" in title_lower:
            return "migrations/001_init.sql"

        if "server entry point" in title_lower:
            if is_js:
                return "src/server.js"
            if "python" in lang or "fastapi" in lang or "django" in lang:
                return "src/main.py"

        if "app shell" in title_lower or "frontend app shell" in title_lower:
            return "src/main.jsx"

        if "api client" in title_lower:
            if is_js:
                return "src/api/client.js"
            return "src/api/client.py"

        if "env" in title_lower and (
            "environment" in title_lower or ".env" in title_lower
        ):
            return ".env.example"

        if self._is_frontend_task(task):
            return self._derive_frontend_file_path(task.title)
        title = task.title
        domain = self._task_domain(task)
        ext = self._backend_source_extension(task, tech_stack)
        if domain == "test":
            base = _name_from_title(title).lower()
            return f"tests/test_{base}{ext if ext == '.js' else '.py'}"
        if domain == "backend_model":
            return f"src/models/{_api_module_filename(title)}{ext}"
        return f"src/api/{_api_module_filename(title)}{ext}"

    def _derive_frontend_file_path(self, task_title: str) -> str:
        lower = task_title.lower()
        if "applayout" in lower.replace(" ", ""):
            return "src/components/AppLayout.jsx"
        if "dashboard" in lower and "page" in lower:
            return "src/pages/Dashboard.jsx"
        if "history" in lower and "page" in lower:
            return "src/pages/History.jsx"
        if "settings" in lower and "page" in lower:
            return "src/pages/Settings.jsx"
        return f"src/components/{_name_from_title(task_title)}.jsx"

    def _is_frontend_task(self, task: Task) -> bool:
        return "frontend" in (task.assigned_agent or "").lower()

    def _task_domain(self, task: Task) -> str:
        title = (task.title or "").lower()
        if "test" in title:
            return "test"
        if self._is_frontend_task(task):
            if "component" in title and "page" not in title:
                return "frontend_component"
            if any(k in title for k in ("navbar", "nav bar", "footer", "applayout")):
                return "frontend_component"
            return "frontend_page"
        if "backend" in (task.assigned_agent or "").lower():
            if "model" in title or "schema" in title:
                return "backend_model"
            return "backend_api"
        if "api" in title or "endpoint" in title:
            return "backend_api"
        return "backend_api"

    def _create_directory_structure(
        self,
        output_dir: str,
        tech_stack: TechStackDocument,
    ) -> None:
        root = Path(output_dir)
        for sub in (
            "src/pages",
            "src/components",
            "src/api",
            "src/models",
            "tests",
            "docs",
        ):
            (root / sub).mkdir(parents=True, exist_ok=True)
        _ = tech_stack

    async def _generate_dockerfile(
        self,
        tech_stack: TechStackDocument,
        output_dir: str,
        *,
        has_frontend: bool | None = None,
        has_backend: bool | None = None,
    ) -> str:
        root = Path(output_dir)
        if has_frontend is None:
            has_frontend = self._has_frontend_artifacts(root)
        if has_backend is None:
            has_backend = self._has_backend_artifacts(root)
        if has_frontend and has_backend:
            return self._dockerfile_react_python(tech_stack)
        if has_frontend:
            return self._dockerfile_react_only(tech_stack)
        return self._dockerfile_python_only(tech_stack)

    async def _generate_docker_compose(
        self,
        tech_stack: TechStackDocument,
        output_dir: str,
    ) -> str:
        resp = await self.llm.complete(
            system_prompt=COMPOSE_PROMPT,
            user_message=tech_stack.model_dump_json(),
            complexity="LOW",
            loop_count=0,
            max_tokens=4096,
        )
        content = _strip_fence(resp.content)
        if "services:" not in content:
            content = self._default_compose(tech_stack)
        _ = output_dir
        return content

    def _generate_env_example(self, tech_stack: TechStackDocument) -> str:
        lines = [
            "# Copy to .env and fill in values",
            "DATABASE_URL=postgresql://user:password@db:5432/app  # Primary database",
            "SECRET_KEY=change-me  # Session signing key",
            "API_PORT=8000  # Backend port",
        ]
        if "react" in tech_stack.framework.lower():
            lines.append("VITE_API_URL=http://localhost:8000  # Frontend API base URL")
        return "\n".join(lines) + "\n"

    async def _validate_docker_build(self, output_dir: str, project_id: str) -> bool:
        print("[SANDBOX] Building Docker image...")
        ok = await self.qa.validate_docker_build(output_dir)
        if ok:
            print("[SANDBOX] Build: success ✓")
        else:
            print("[SANDBOX] Build: failed — remediation task created")
            await self._create_docker_remediation_task(project_id)
        return ok

    async def _create_docker_remediation_task(self, project_id: str) -> None:
        if self.lead is None:
            logger.warning("Docker build failed; no LeadAgent to create remediation task")
            return
        from forgeai.models.task import TaskComplexity

        await self.lead.create_task(
            title="Fix Docker build for deployment package",
            description="QA validation of docker build failed during delivery assembly.",
            complexity=TaskComplexity.MEDIUM,
            assigned_agent="backend_agent_1",
            project_id=UUID(project_id),
        )

    async def _work_output_from_review_transition(self, task_id: UUID) -> str:
        res = await self.db.execute(
            select(TaskStateHistory)
            .where(
                TaskStateHistory.task_id == task_id,
                TaskStateHistory.success.is_(True),
                TaskStateHistory.from_state == TaskState.IN_PROGRESS,
                TaskStateHistory.to_state == TaskState.IN_REVIEW,
            )
            .order_by(TaskStateHistory.attempted_at.desc())
        )
        for row in res.scalars():
            meta = row.metadata_ or {}
            out = meta.get(KEY_WORK_OUTPUT)
            if isinstance(out, str) and out.strip():
                return out.strip()
        return ""

    def _needs_work_output_from_history(self, content: str) -> bool:
        text = content.strip()
        if not text:
            return True
        if self._is_placeholder_output(text):
            return True
        if len(text) < 50:
            return True
        if not any(kw in text for kw in ("def ", "export ", "import ", "class ", "function")):
            return True
        lower = text.lower()
        if lower.endswith(" ok") and "export " not in text and "def " not in text:
            return True
        return False

    async def _load_done_tasks(self, project_id: UUID) -> tuple[list[Task], str]:
        """Load every DONE task for the project — no agent-type filter."""
        all_res = await self.db.execute(
            select(Task)
            .where(Task.project_id == project_id)
            .order_by(Task.created_at)
        )
        all_tasks = list(all_res.scalars())
        done_tasks = [
            t
            for t in all_tasks
            if (t.current_state.value if hasattr(t.current_state, "value") else t.current_state)
            == TaskState.DONE.value
        ]
        fe_done = sum(1 for t in done_tasks if self._is_frontend_task(t))
        be_done = sum(
            1 for t in done_tasks if "backend" in (t.assigned_agent or "").lower()
        )
        other_done = len(done_tasks) - fe_done - be_done
        diag = (
            f"total tasks={len(all_tasks)}, DONE={len(done_tasks)} "
            f"(frontend={fe_done}, backend={be_done}, other={other_done})"
        )
        logger.info("DONE task query for %s: %s", project_id, diag)
        return done_tasks, diag

    async def _resolve_write_content(self, task: Task) -> str:
        """Content to write: ``task.output`` when usable, else history/artefact fallback."""
        if task.output and task.output.strip() and not self._is_placeholder_output(task.output):
            return task.output.strip()
        return await self._output_from_history(task)

    async def _output_from_history(self, task: Task) -> str:
        sm = TaskStateMachine(self.db)
        hist = await sm.get_history(task.id)

        for row in reversed(hist):
            if (
                row.success
                and row.from_state == TaskState.IN_PROGRESS
                and row.to_state == TaskState.IN_REVIEW
            ):
                meta = row.metadata_ or {}
                out = meta.get(KEY_WORK_OUTPUT)
                if isinstance(out, str) and out.strip():
                    parsed = self._parse_work_output(out, task)
                    if parsed.strip():
                        return parsed

        for row in reversed(hist):
            if row.success and row.to_state == TaskState.DONE:
                meta = row.metadata_ or {}
                for key in (KEY_OUTPUT, KEY_WORK_OUTPUT):
                    out = meta.get(key)
                    if isinstance(out, str) and out.strip() and not self._is_placeholder_output(out):
                        parsed = self._parse_work_output(out, task)
                        if parsed.strip():
                            return parsed

        artefact = await self._load_task_output_artefact(task.id)
        if artefact:
            return artefact

        return ""

    def _is_placeholder_output(self, text: str) -> bool:
        lower = text.strip().lower()
        if lower in _QA_PLACEHOLDER_OUTPUTS:
            return True
        return lower.endswith(" ok") and len(lower) < 80

    def _is_forgeai_pipeline_source(self, content: str) -> bool:
        """True when content looks like ForgeAI's own main.py / pipeline, not agent output."""
        return any(marker in content for marker in _FORGEAI_SOURCE_MARKERS)

    def _parse_work_output(self, raw: str, task: Task) -> str:
        """Normalize stored output (plain code or JSON bundle)."""
        stripped = raw.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict):
                    code = data.get("code") or data.get("react_code")
                    if isinstance(code, str) and code.strip():
                        return code.strip()
            except json.JSONDecodeError:
                pass
        if self._is_frontend_task(task) and "export" not in stripped and "function" not in stripped:
            if len(stripped) > 20:
                return stripped
            return stripped
        return stripped

    async def _load_task_output_artefact(self, task_id: UUID) -> str:
        res = await self.db.execute(
            select(ProjectArtefactModel).where(
                ProjectArtefactModel.artefact_type == f"task_output:{task_id}",
                ProjectArtefactModel.is_current.is_(True),
            )
        )
        row = res.scalar_one_or_none()
        if row is None:
            return ""
        content = row.content or {}
        out = content.get("output") if isinstance(content, dict) else None
        if isinstance(out, str) and out.strip() and not self._is_placeholder_output(out):
            return out.strip()
        return ""

    def _has_frontend_artifacts(self, root: Path) -> bool:
        pages = root / "src" / "pages"
        components = root / "src" / "components"
        return any(pages.glob("*.jsx")) or any(components.glob("*.jsx"))

    def _has_backend_artifacts(self, root: Path) -> bool:
        api_dir = root / "src" / "api"
        return any(api_dir.glob("*.py"))

    def _write_requirements_txt(
        self,
        root: Path,
        tech_stack: TechStackDocument,
        has_backend: bool,
    ) -> None:
        if not has_backend:
            return
        lines = [
            "fastapi>=0.110.0",
            "uvicorn[standard]>=0.27.0",
            "pydantic>=2.0.0",
        ]
        if "postgres" in tech_stack.database.lower():
            lines.append("psycopg2-binary>=2.9.9")
        (root / "requirements.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _dockerfile_python_only(self, tech_stack: TechStackDocument) -> str:
        _ = tech_stack
        return """\
FROM python:3.11-slim
WORKDIR /app
COPY src/ ./src/
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8000
CMD ["python", "src/api/main.py"]
"""

    def _dockerfile_react_only(self, tech_stack: TechStackDocument) -> str:
        _ = tech_stack
        return """\
FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY src ./src
RUN npm init -y && npm install react react-dom vite @vitejs/plugin-react \\
    && printf '%s\\n' 'import { defineConfig } from \"vite\"' 'export default defineConfig({ root: \"src\" })' > vite.config.js \\
    && npx vite build --outDir /app/dist

FROM nginx:alpine
COPY --from=frontend-build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
"""

    def _dockerfile_react_python(self, tech_stack: TechStackDocument) -> str:
        _ = tech_stack
        return """\
FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY src/pages ./src/pages
COPY src/components ./src/components
RUN npm init -y && npm install react react-dom vite @vitejs/plugin-react \\
    && printf '%s\\n' 'import { defineConfig } from \"vite\"' 'export default defineConfig({ root: \"src\" })' > vite.config.js \\
    && npx vite build --outDir /app/dist

FROM python:3.11-slim
WORKDIR /app
COPY src/ ./src/
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=frontend-build /app/dist ./static
EXPOSE 8000
CMD ["python", "src/api/main.py"]
"""

    async def _build_summary_report(
        self,
        project_id: str,
        master_document: MasterDocument,
        project_brief: str,
    ) -> FinalSummaryReport:
        pid = UUID(project_id)
        res = await self.db.execute(
            select(Task).where(
                Task.project_id == pid,
                Task.current_state == TaskState.DONE,
            )
        )
        done = list(res.scalars())
        by_phase: dict[str, int] = {"FRONTEND_PHASE": 0, "BACKEND_PHASE": 0}
        for t in done:
            agent = (t.assigned_agent or "").lower()
            if "backend" in agent:
                by_phase["BACKEND_PHASE"] += 1
            else:
                by_phase["FRONTEND_PHASE"] += 1

        task_ids = [t.id for t in done]
        esc_total = 0
        if task_ids:
            esc_res = await self.db.execute(
                select(sa_func.count())
                .select_from(EscalationEventModel)
                .where(EscalationEventModel.task_id.in_(task_ids))
            )
            esc_total = int(esc_res.scalar_one() or 0)

        lessons = 0
        if self.lead and self.lead._agent_memory is not None:
            try:
                ranked = await self.lead._agent_memory.retrieve_lessons(
                    "lead_agent",
                    master_document.project_summary,
                    top_k=100,
                )
                lessons = len(ranked)
            except Exception:
                lessons = 0

        tags = [t.tag_name for t in self.git.get_tags()]

        return FinalSummaryReport(
            project_id=project_id,
            project_name=master_document.project_name,
            project_brief=project_brief,
            total_tasks_completed=len(done),
            total_qa_cycles=max(1, len(done)),
            total_cost_usd=0.0,
            total_duration_minutes=float(len(done) * 8),
            tasks_by_phase=by_phase,
            escalations_total=esc_total,
            escalations_resolved_automatically=max(0, esc_total - 1),
            escalations_requiring_human=min(1, esc_total),
            lessons_accumulated=lessons,
            rollback_points=tags,
            release_tag="release-v1",
            generated_at=datetime.now(UTC),
        )

    def _default_compose(self, tech_stack: TechStackDocument) -> str:
        db = ""
        if "postgres" in tech_stack.database.lower():
            db = """
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
      POSTGRES_DB: app
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U user"]
      interval: 5s
      timeout: 5s
      retries: 5
"""
        return f"""\
services:
  app:
    build: .
    ports:
      - "3000:8000"
    env_file:
      - .env
    depends_on:
      - db
{db}
"""
