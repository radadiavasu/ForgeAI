"""Backend developer agent — implementation via LLM (Phase 5)."""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import select

from forgeai.agents.base import BaseAgent
from forgeai.llm.client import LLMClient
from forgeai.models.task import Task
from forgeai.memory.agent_memory import AgentMemory
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_WORK_OUTPUT

logger = logging.getLogger(__name__)

BACKEND_ROLE_PROMPT = """
You are Backend_Agent. Implement the assigned task as Python application code suitable
for a module named main.py in a sandbox. Prefer clear functions and minimal dependencies.
Output only the Python source code for main.py (no markdown fences unless wrapping code).
If tests expect specific function names, implement exactly what the task asks for.
""".strip()


def _extract_code_from_response(text: str) -> str:
    t = text.strip()
    m = re.search(r"```(?:python)?\s*\n([\s\S]*?)```", t)
    if m:
        return m.group(1).strip()
    return t


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
        system_prompt = f"{BACKEND_ROLE_PROMPT}\n\nRelevant past lessons:\n{lessons_block}"
        user_message = (
            f"Task:\n{task_description}\n\n"
            f"Master document context:\n{master_document_section}\n"
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
        code = _extract_code_from_response(resp.content)
        lines = len(code.splitlines())
        logger.info("[BACKEND] Code generated — %s lines", lines)

        machine = TaskStateMachine(self.db, task_memory=self.task_memory)
        return await machine.transition(
            task_id,
            TaskState.IN_REVIEW,
            self.agent_id,
            **{KEY_WORK_OUTPUT: code},
        )
