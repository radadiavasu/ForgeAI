"""Component registry backed by ``project_artefacts`` (Req 27, Phase 6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from forgeai.contracts.schemas import ComponentEntry
from forgeai.exceptions import DuplicateComponentError
from forgeai.models.project_artefact import ProjectArtefactModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_REGISTRY_TYPE = "component_registry_entry"


class ComponentRegistry:
    """Register and query reusable UI components per project."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    def _pid(self, project_id: str | uuid.UUID) -> uuid.UUID:
        return uuid.UUID(str(project_id))

    async def register(
        self,
        project_id: str | uuid.UUID,
        component_name: str,
        owner_agent_id: str,
        interface_definition: str,
        file_path: str,
    ) -> ComponentEntry:
        existing = await self.query(project_id, component_name)
        if existing is not None:
            raise DuplicateComponentError(
                f"Component {component_name!r} is already registered for this project"
            )
        pid = self._pid(project_id)
        now = datetime.now(UTC)
        entry = ComponentEntry(
            component_name=component_name,
            owner_agent_id=owner_agent_id,
            interface_definition=interface_definition,
            file_path=file_path,
            project_id=str(pid),
            registered_at=now,
            used_by=[],
        )
        row = ProjectArtefactModel(
            project_id=pid,
            artefact_type=_REGISTRY_TYPE,
            content=entry.model_dump(mode="json"),
            version=1,
            is_current=True,
            created_by=owner_agent_id,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return ComponentEntry.model_validate(row.content)

    async def query(
        self, project_id: str | uuid.UUID, component_name: str
    ) -> ComponentEntry | None:
        pid = self._pid(project_id)
        stmt = (
            select(ProjectArtefactModel)
            .where(
                ProjectArtefactModel.project_id == pid,
                ProjectArtefactModel.artefact_type == _REGISTRY_TYPE,
                ProjectArtefactModel.content["component_name"].astext == component_name,
            )
            .order_by(ProjectArtefactModel.created_at.desc())
            .limit(1)
        )
        res = await self.db.execute(stmt)
        row = res.scalar_one_or_none()
        if row is None:
            return None
        return ComponentEntry.model_validate(row.content)

    async def list_all(self, project_id: str | uuid.UUID) -> list[ComponentEntry]:
        pid = self._pid(project_id)
        stmt = (
            select(ProjectArtefactModel)
            .where(
                ProjectArtefactModel.project_id == pid,
                ProjectArtefactModel.artefact_type == _REGISTRY_TYPE,
            )
            .order_by(ProjectArtefactModel.created_at.asc())
        )
        res = await self.db.execute(stmt)
        rows = res.scalars().all()
        return [ComponentEntry.model_validate(r.content) for r in rows]

    async def mark_used_by(
        self,
        project_id: str | uuid.UUID,
        component_name: str,
        consumer_agent_id: str,
    ) -> None:
        pid = self._pid(project_id)
        stmt = select(ProjectArtefactModel).where(
            ProjectArtefactModel.project_id == pid,
            ProjectArtefactModel.artefact_type == _REGISTRY_TYPE,
            ProjectArtefactModel.content["component_name"].as_string() == component_name,
        )
        res = await self.db.execute(stmt)
        row = res.scalar_one_or_none()
        if row is None:
            return
        data = dict(row.content)
        used = list(data.get("used_by") or [])
        if consumer_agent_id not in used:
            used.append(consumer_agent_id)
        data["used_by"] = used
        row.content = data
        await self.db.commit()
