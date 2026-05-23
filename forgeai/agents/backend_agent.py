"""Backend developer agent — implementation via LLM (Phase 5)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select

from forgeai.agents.base import BaseAgent
from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import TechStackDocument
from forgeai.models.project_artefact import ProjectArtefactModel
from forgeai.models.task import Task
from forgeai.memory.agent_memory import AgentMemory
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_METADATA, KEY_WORK_OUTPUT

logger = logging.getLogger(__name__)

BACKEND_ROLE_PROMPT = """
You are Backend_Agent. Implement the assigned task as application code suitable for
sandbox execution. Use only the language, framework, and libraries from the mandatory
tech stack when it is provided.

When an API_Contract is provided, your implementation is bound by it. The endpoint path,
HTTP method, request schema, and response schema are not suggestions — they are the
contract. QA_Agent will validate your output against the contract. Any deviation will be
rejected as a defect.

Output JSON with fields:
- "code": complete implementation source for the primary module (e.g. main.py or index.js)
- "test_code": tests using the project's testing framework

If you cannot emit JSON, output only the implementation source.
""".strip()


async def load_tech_stack_document(db_session, project_id: uuid.UUID) -> TechStackDocument | None:
    """Load current Tech_Stack_Document from project artefacts (same pattern as delivery)."""
    row = (
        await db_session.execute(
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


def format_critical_language_block(tech_stack: TechStackDocument) -> str:
    """Explicit language/framework guard placed immediately before the task description."""
    lang = tech_stack.language
    framework = tech_stack.framework
    return f"""CRITICAL: You are generating {lang} code.
Framework: {framework}
You MUST write code using {framework}.
DO NOT deviate from the specified language and framework.
File extension must match the language:
  JavaScript/TypeScript → .js or .ts
  Python → .py
  Other → appropriate extension for {lang}
""".strip()


def format_mandatory_tech_stack_block(tech_stack: TechStackDocument) -> str:
    """Mandatory constraint block injected at the top of code-generation prompts."""
    libs = ", ".join(tech_stack.libraries) if tech_stack.libraries else "(none listed)"
    return (
        "MANDATORY TECH STACK — you must use these exact technologies:\n"
        f"  Language: {tech_stack.language}\n"
        f"  Framework: {tech_stack.framework}\n"
        f"  Database: {tech_stack.database}\n"
        f"  Testing: {tech_stack.testing_framework}\n"
        f"  Libraries: {libs}\n\n"
        "DO NOT use any other language or framework.\n"
        "If language is JavaScript/Node.js, write JavaScript not Python.\n"
        "If framework is Express.js, use Express.js not http.server."
    )


def _test_code_field_description(tech_stack: TechStackDocument) -> str:
    tf = tech_stack.testing_framework.lower()
    lang = tech_stack.language.lower()
    if "vitest" in tf:
        return "complete JavaScript test module using Vitest (import from vitest)"
    if "jest" in tf:
        return "complete JavaScript test module using Jest"
    if "pytest" in tf or "python" in lang:
        return "pytest module that imports from main and validates behaviour"
    return f"test module using {tech_stack.testing_framework}"


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


def _extract_code_from_response(text: str) -> tuple[str, str]:
    t = text.strip()
    try:
        data = _extract_json_object(t)
        code = str(data.get("code", "")).strip()
        test_code = str(data.get("test_code", "")).strip()
        if code:
            return code, test_code
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    m = re.search(r"```(?:python)?\s*\n([\s\S]*?)```", t)
    if m:
        return m.group(1).strip(), ""
    return t, ""


class BackendAgent(BaseAgent):
    """Completes backend tasks using LLM-generated code or legacy fixed output."""

    def __init__(
        self,
        agent_id: str,
        db_session,
        *,
        task_memory=None,
        llm_client: LLMClient | None = None,
        agent_memory: AgentMemory | None = None,
    ) -> None:
        super().__init__(agent_id, db_session, task_memory=task_memory)
        self.llm = llm_client
        self.memory = agent_memory
        self.agent_role = "backend_agent"

    async def complete_work(
        self,
        task_id: uuid.UUID,
        *,
        task_description: str | None = None,
        master_document_section: str | None = None,
        api_contract: dict | None = None,
        loop_count: int = 0,
        output: str | None = None,
    ) -> Task:
        """Transition ``IN_PROGRESS`` → ``IN_REVIEW`` with LLM code or legacy output string."""
        if output is not None:
            machine = TaskStateMachine(self.db, task_memory=self.task_memory)
            return await machine.transition(
                task_id,
                TaskState.IN_REVIEW,
                self.agent_id,
                **{KEY_WORK_OUTPUT: output},
            )

        if self.llm is None or self.memory is None:
            raise RuntimeError(
                "LLM completion requires llm_client and agent_memory; "
                "or pass output= for transition-only mode."
            )
        if task_description is None or master_document_section is None:
            raise ValueError("task_description and master_document_section are required for LLM mode")

        result = await self.db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one()
        complexity = task.complexity.value if hasattr(task.complexity, "value") else str(task.complexity)

        ranked = await self.memory.retrieve_lessons(
            self.agent_role,
            f"{task_description}\n{master_document_section}",
            top_k=5,
        )
        lesson_lines = [f"- {x.lesson.rule}" for x in ranked[:5]]
        lessons_block = "\n".join(lesson_lines) if lesson_lines else "(no prior lessons)"
        tech_stack = await load_tech_stack_document(self.db, task.project_id)
        system_prompt = f"{BACKEND_ROLE_PROMPT}\n\nRelevant past lessons:\n{lessons_block}"
        if tech_stack:
            system_prompt += (
                f'\n\nFor this project, "test_code" must be a '
                f"{_test_code_field_description(tech_stack)}."
            )
        contract_block = ""
        if api_contract:
            contract_block = (
                "\n\nAPI_Contract (must match exactly):\n"
                f"{json.dumps(api_contract, indent=2)}\n\n"
                "Your implementation must exactly match the API_Contract endpoint, method, "
                "request schema, and response schema. Any deviation is a defect."
            )
        tech_block = ""
        critical_block = ""
        if tech_stack:
            tech_block = format_mandatory_tech_stack_block(tech_stack) + "\n\n"
            critical_block = format_critical_language_block(tech_stack) + "\n\n"
        user_message = (
            f"{tech_block}"
            f"{critical_block}"
            f"Task:\n{task_description}\n\n"
            f"Master document context:\n{master_document_section}\n"
            f"{contract_block}"
        )

        logger.info(
            "[BACKEND] Generating implementation via LLM (complexity=%s loop=%s)...",
            complexity,
            loop_count,
        )
        resp = await self.llm.complete(
            system_prompt=system_prompt,
            user_message=user_message,
            complexity=complexity,
            loop_count=loop_count,
            max_tokens=8192,
        )
        code, test_code = _extract_code_from_response(resp.content)
        lines = len(code.splitlines())
        logger.info("[BACKEND] Code generated — %s lines", lines)

        metadata: dict[str, Any] = {}
        if test_code:
            metadata["test_code"] = test_code

        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        kwargs: dict[str, Any] = {KEY_WORK_OUTPUT: code}
        if metadata:
            kwargs[KEY_METADATA] = metadata
        return await machine.transition(
            task_id,
            TaskState.IN_REVIEW,
            self.agent_id,
            **kwargs,
        )
