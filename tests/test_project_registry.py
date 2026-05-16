"""ProjectRegistry tests."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.lifecycle.project_registry import ProjectRegistry
from forgeai.lifecycle.schemas import ProjectStatus


@pytest.mark.asyncio
async def test_create_project_active(db_session: AsyncSession) -> None:
    reg = ProjectRegistry(db_session)
    p = await reg.create_project("Task Manager", "Build tasks app")
    assert p.status == ProjectStatus.ACTIVE
    assert p.name == "Task Manager"


@pytest.mark.asyncio
async def test_set_live_transitions_and_sets_fields(db_session: AsyncSession) -> None:
    reg = ProjectRegistry(db_session)
    p = await reg.create_project("App", "brief")
    live = await reg.set_live(p.id, "release-v1")
    assert live.status == ProjectStatus.LIVE
    assert live.delivered_at is not None
    assert live.release_tag == "release-v1"


@pytest.mark.asyncio
async def test_set_archived(db_session: AsyncSession) -> None:
    reg = ProjectRegistry(db_session)
    p = await reg.create_project("App", "brief")
    await reg.set_live(p.id, "v1")
    archived = await reg.set_archived(p.id)
    assert archived.status == ProjectStatus.ARCHIVED
    assert archived.archived_at is not None


@pytest.mark.asyncio
async def test_ensure_active_project_with_fixed_id(db_session: AsyncSession) -> None:
    import uuid

    reg = ProjectRegistry(db_session)
    pid = str(uuid.uuid4())
    p = await reg.ensure_active_project(pid, "Fixed", "brief")
    assert p.id == pid
    assert p.status == ProjectStatus.ACTIVE
    again = await reg.ensure_active_project(pid, "Other", "other")
    assert again.id == pid
    assert again.name == "Fixed"


@pytest.mark.asyncio
async def test_list_live_and_active(db_session: AsyncSession) -> None:
    reg = ProjectRegistry(db_session)
    a = await reg.create_project("Active One", "b")
    b = await reg.create_project("Live One", "b")
    await reg.set_live(b.id, "v1")
    assert len(await reg.list_active_projects()) >= 1
    assert any(p.id == a.id for p in await reg.list_active_projects())
    live = await reg.list_live_projects()
    assert any(p.id == b.id for p in live)
