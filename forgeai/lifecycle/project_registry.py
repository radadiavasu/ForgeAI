"""Project lifecycle registry — ACTIVE, LIVE, ARCHIVED (Phase 9B)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from forgeai.lifecycle.schemas import Project, ProjectStatus
from forgeai.models.project import ProjectModel


class ProjectRegistry:
    """Persist and query project lifecycle state."""

    def __init__(self, db_session) -> None:
        self.db = db_session

    def _to_schema(self, row: ProjectModel) -> Project:
        return Project(
            id=str(row.id),
            name=row.name,
            brief=row.brief,
            status=ProjectStatus(row.status),
            created_at=row.created_at,
            delivered_at=row.delivered_at,
            archived_at=row.archived_at,
            release_tag=row.release_tag,
        )

    async def ensure_active_project(
        self, project_id: str, name: str, brief: str
    ) -> Project:
        """Create ACTIVE project row with a fixed id if missing."""
        existing = await self.get_project(project_id)
        if existing is not None:
            return existing
        row = ProjectModel(
            id=uuid.UUID(project_id),
            name=name,
            brief=brief,
            status=ProjectStatus.ACTIVE.value,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return self._to_schema(row)

    async def create_project(self, name: str, brief: str) -> Project:
        row = ProjectModel(
            name=name,
            brief=brief,
            status=ProjectStatus.ACTIVE.value,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return self._to_schema(row)

    async def get_project(self, project_id: str) -> Project | None:
        pid = uuid.UUID(project_id)
        res = await self.db.execute(select(ProjectModel).where(ProjectModel.id == pid))
        row = res.scalar_one_or_none()
        return self._to_schema(row) if row else None

    async def set_live(self, project_id: str, release_tag: str) -> Project:
        pid = uuid.UUID(project_id)
        res = await self.db.execute(select(ProjectModel).where(ProjectModel.id == pid))
        row = res.scalar_one()
        if row.status != ProjectStatus.ACTIVE.value:
            raise ValueError(f"Cannot set LIVE from status {row.status}")
        row.status = ProjectStatus.LIVE.value
        row.delivered_at = datetime.now(UTC)
        row.release_tag = release_tag
        await self.db.commit()
        await self.db.refresh(row)
        return self._to_schema(row)

    async def set_archived(self, project_id: str) -> Project:
        pid = uuid.UUID(project_id)
        res = await self.db.execute(select(ProjectModel).where(ProjectModel.id == pid))
        row = res.scalar_one()
        if row.status != ProjectStatus.LIVE.value:
            raise ValueError(f"Cannot archive from status {row.status}")
        row.status = ProjectStatus.ARCHIVED.value
        row.archived_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(row)
        return self._to_schema(row)

    async def list_live_projects(self) -> list[Project]:
        res = await self.db.execute(
            select(ProjectModel).where(ProjectModel.status == ProjectStatus.LIVE.value)
        )
        return [self._to_schema(r) for r in res.scalars()]

    async def list_active_projects(self) -> list[Project]:
        res = await self.db.execute(
            select(ProjectModel).where(ProjectModel.status == ProjectStatus.ACTIVE.value)
        )
        return [self._to_schema(r) for r in res.scalars()]
