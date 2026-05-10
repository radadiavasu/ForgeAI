"""Lead agent: task creation, lifecycle transitions, project artefacts."""

from __future__ import annotations

import uuid

from sqlalchemy import func as sa_func, select, update

from forgeai.agents.base import BaseAgent
from forgeai.llm.schemas import MasterDocument, TechStackDocument
from forgeai.models.project_artefact import ProjectArtefactModel
from forgeai.models.task import Task, TaskComplexity
from forgeai.state_machine.machine import TaskStateMachine
from forgeai.state_machine.states import TaskState
from forgeai.state_machine.transitions import KEY_PHASE_APPROVAL


class LeadAgent(BaseAgent):
    """Creates tasks and performs lead-side transitions."""

    async def create_task(
        self,
        title: str,
        description: str | None,
        complexity: TaskComplexity,
        assigned_agent: str,
        project_id: uuid.UUID | None = None,
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
