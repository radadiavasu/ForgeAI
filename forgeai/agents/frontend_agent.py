"""Frontend agent — React + Tailwind via LLM (Phase 6)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from pydantic import ValidationError

from sqlalchemy import select

from forgeai.agents.base import BaseAgent
from forgeai.contracts.registry import ComponentRegistry
from forgeai.contracts.schemas import (
    FrontendOutput,
    LayoutSpecification,
    NavigationContract,
    PageSpec,
    RouteDefinition,
)
from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import MasterDocument
from forgeai.memory.agent_memory import AgentMemory
from forgeai.models.project_artefact import ProjectArtefactModel
from forgeai.models.task import Task
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_METADATA, KEY_WORK_OUTPUT

logger = logging.getLogger(__name__)

FRONTEND_ROLE_PROMPT = """
You are Frontend_Agent, a specialist in building React user
interface components. You receive a task specification, a
layout specification, a Navigation_Contract, and a
Component_Registry query result.

You produce complete, working React components using
Tailwind CSS for styling. Your code must be importable,
correctly typed with PropTypes, and follow the
Navigation_Contract's linking convention exactly.

Before building any component, you check the Component_Registry.
If a suitable component already exists, you import it.
You never rebuild what already exists.

After completing a component, you register it in the
Component_Registry if it is reusable.

You output structured JSON with two fields:
- "code": the complete React component code as a string
- "test_code": basic test code as a string
- "components_registered": list of component names you built
- "components_imported": list of component names you imported
""".strip()


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
        return out if isinstance(out, dict) else {"raw": out}
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            out = json.loads(m.group(0))
            return out if isinstance(out, dict) else {"raw": out}
        raise


def _normalize_frontend_output_payload(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    for key in ("code", "test_code", "file_path"):
        if key in out and out[key] is not None and not isinstance(out[key], str):
            out[key] = str(out[key])
    for key in ("components_registered", "components_imported"):
        if key not in out or out[key] is None:
            out[key] = []
        elif isinstance(out[key], str):
            out[key] = [out[key]] if out[key].strip() else []
        elif not isinstance(out[key], list):
            out[key] = [str(out[key])]
        else:
            out[key] = [str(x) for x in out[key]]
    return out


class FrontendAgent(BaseAgent):
    """Builds React UI from task and layout context."""

    def __init__(
        self,
        agent_id: str,
        db_session,
        llm_client: LLMClient | None = None,
        agent_memory: AgentMemory | None = None,
        component_registry: ComponentRegistry | None = None,
        navigation_contract: NavigationContract | None = None,
        *,
        task_memory=None,
    ) -> None:
        super().__init__(agent_id, db_session, task_memory=task_memory)
        self.llm = llm_client
        self.memory = agent_memory
        self.registry = component_registry
        self.nav_contract = navigation_contract
        self.agent_role = "frontend_agent"

    async def propose_routes(self, layout_spec: LayoutSpecification) -> list[RouteDefinition]:
        if self.llm is None:
            return self._default_route_proposal(layout_spec)
        user_message = (
            f"You are {self.agent_id}. Propose routes you should own as JSON array of objects "
            f"with keys path, component_name, is_root_layout (boolean). "
            f"Layout pages:\n{layout_spec.model_dump_json()}"
        )
        resp = await self.llm.complete(
            system_prompt=FRONTEND_ROLE_PROMPT,
            user_message=user_message,
            complexity="LOW",
            loop_count=0,
            max_tokens=2048,
        )
        try:
            raw = json.loads(_strip_json_fence(resp.content))
            if isinstance(raw, dict):
                raw = raw.get("routes") or raw.get("proposed") or []
            if not isinstance(raw, list):
                return self._default_route_proposal(layout_spec)
            out: list[RouteDefinition] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path", "/")).strip() or "/"
                comp = str(item.get("component_name", "Page")).strip() or "Page"
                root = bool(item.get("is_root_layout", False))
                out.append(
                    RouteDefinition(
                        path=path,
                        owner_agent_id=self.agent_id,
                        component_name=comp,
                        is_root_layout=root,
                    )
                )
            return out or self._default_route_proposal(layout_spec)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            return self._default_route_proposal(layout_spec)

    def _default_route_proposal(self, layout_spec: LayoutSpecification) -> list[RouteDefinition]:
        pages = layout_spec.pages or []
        if not pages:
            return [
                RouteDefinition(
                    path="/",
                    owner_agent_id=self.agent_id,
                    component_name="DashboardPage",
                    is_root_layout=True,
                )
            ]
        idx = 0
        if self.agent_id.endswith("_1"):
            chunk = pages[: max(1, len(pages) - 1)]
        elif self.agent_id.endswith("_2"):
            chunk = pages[-1:]
        else:
            chunk = pages
        routes: list[RouteDefinition] = []
        for p in chunk:
            idx += 1
            routes.append(
                RouteDefinition(
                    path=p.route or f"/{p.name.lower()}",
                    owner_agent_id=self.agent_id,
                    component_name=f"{p.name.replace(' ', '')}Page",
                    is_root_layout=idx == 1 and self.agent_id.endswith("_1"),
                )
            )
        return routes

    async def _load_master_document(self, project_id: uuid.UUID) -> MasterDocument | None:
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

    def _api_wiring_section(self, master_document: MasterDocument) -> str:
        if not master_document.api_surfaces:
            return ""
        endpoint_lines = "\n".join(
            f"  {s.method} {s.endpoint} — {s.description}"
            for s in master_document.api_surfaces
        )
        return f"""
IMPORTANT: This component connects to a backend REST API.
Do NOT use local state for data that comes from the API.
Use fetch() or axios for all data operations.
API base URL: import.meta.env.VITE_API_URL ?? '/api'

Available endpoints:
{endpoint_lines}

For every API call:
- Show loading state while request is in flight
- Show error message if request fails
- Update UI immediately after successful mutation
- No full page reloads — SPA behavior required
"""

    async def complete_work(
        self,
        task_id: uuid.UUID,
        task_description: str,
        page_spec: PageSpec,
        loop_count: int = 0,
    ) -> Task:
        if self.llm is None or self.registry is None or self.memory is None:
            raise RuntimeError("FrontendAgent requires llm_client, component_registry, and agent_memory")
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one()
        project_id = str(task.project_id)
        existing: list[str] = []
        if self.nav_contract:
            project_id = self.nav_contract.project_id
        if project_id:
            all_entries = await self.registry.list_all(project_id)
            existing = [e.component_name for e in all_entries]

        master_document = await self._load_master_document(task.project_id)
        api_section = ""
        if master_document is not None:
            api_section = self._api_wiring_section(master_document)

        ranked = await self.memory.retrieve_lessons(
            self.agent_role,
            f"{task_description}\n{page_spec.model_dump_json()}",
            top_k=5,
        )
        lesson_lines = [f"- {x.lesson.rule}" for x in ranked[:5]]
        lessons_block = "\n".join(lesson_lines) if lesson_lines else "(no prior lessons)"

        if loop_count > 0 and self.task_memory is not None:
            try:
                defect_json = await self.task_memory.get(
                    str(task_id), "defect_report"
                )
                if defect_json:
                    import json as _json

                    defect = _json.loads(defect_json)
                    defect_block = (
                        "PREVIOUS ATTEMPT FAILED. Fix these specific issues:\n"
                        f"Summary: {defect.get('failure_summary', '')}\n\n"
                        f"Required fixes:\n{defect.get('suggestions', '')}\n\n"
                        "Failed tests:\n"
                        + "\n".join(defect.get("failed_tests", []))
                        + "\n\nOriginal task:\n"
                    )
                    task_description = defect_block + (task_description or "")
            except Exception:
                pass

        nav_block = self.nav_contract.model_dump_json() if self.nav_contract else "{}"
        user_message = (
            f"Task:\n{task_description}\n\n"
            f"{api_section}\n"
            f"PageSpec:\n{page_spec.model_dump_json()}\n\n"
            f"Navigation_Contract:\n{nav_block}\n\n"
            f"Existing registry components: {existing}\n\n"
            "Output JSON only with keys: code, test_code, components_registered, "
            "components_imported, file_path."
        )
        resp = await self.llm.complete(
            system_prompt=f"{FRONTEND_ROLE_PROMPT}\n\nRelevant lessons:\n{lessons_block}",
            user_message=user_message,
            complexity="LOW",
            loop_count=loop_count,
            max_tokens=8192,
        )
        raw = _normalize_frontend_output_payload(_extract_json_object(resp.content))
        output = FrontendOutput.model_validate(raw)

        if project_id:
            for name in output.components_imported:
                entry = await self.registry.query(project_id, name)
                if entry is not None:
                    await self.registry.mark_used_by(project_id, name, self.agent_id)
            for name in output.components_registered:
                await self.registry.register(
                    project_id,
                    component_name=name,
                    owner_agent_id=self.agent_id,
                    interface_definition=f"Reusable UI: {name}",
                    file_path=output.file_path,
                    source_code=output.code,
                )

        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        return await machine.transition(
            task_id,
            TaskState.IN_REVIEW,
            self.agent_id,
            **{
                KEY_WORK_OUTPUT: output.code,
                KEY_METADATA: {
                    "frontend_test_code": output.test_code,
                    "frontend_file_path": output.file_path,
                    "components_registered": output.components_registered,
                    "components_imported": output.components_imported,
                },
            },
        )
