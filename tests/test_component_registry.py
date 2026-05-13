"""Component registry (Phase 6)."""

from __future__ import annotations

import uuid

import pytest

from forgeai.contracts.registry import ComponentRegistry
from forgeai.exceptions import DuplicateComponentError


@pytest.mark.asyncio
async def test_register_query_list(db_session):
    reg = ComponentRegistry(db_session)
    pid = str(uuid.uuid4())
    e = await reg.register(pid, "NavBar", "fe1", "props: items", "src/NavBar.jsx")
    assert e.component_name == "NavBar"
    q = await reg.query(pid, "NavBar")
    assert q is not None and q.file_path.endswith("NavBar.jsx")
    assert await reg.query(pid, "Missing") is None
    all_e = await reg.list_all(pid)
    assert len(all_e) == 1


@pytest.mark.asyncio
async def test_duplicate_raises(db_session):
    reg = ComponentRegistry(db_session)
    pid = str(uuid.uuid4())
    await reg.register(pid, "X", "a", "i", "p")
    with pytest.raises(DuplicateComponentError):
        await reg.register(pid, "X", "b", "i", "p2")


@pytest.mark.asyncio
async def test_mark_used_by(db_session):
    reg = ComponentRegistry(db_session)
    pid = str(uuid.uuid4())
    await reg.register(pid, "Card", "fe1", "i", "c.jsx")
    await reg.mark_used_by(pid, "Card", "fe2")
    q = await reg.query(pid, "Card")
    assert "fe2" in (q.used_by if q else [])


@pytest.mark.asyncio
async def test_components_scoped_by_project(db_session):
    reg = ComponentRegistry(db_session)
    p1 = str(uuid.uuid4())
    p2 = str(uuid.uuid4())
    await reg.register(p1, "Z", "a", "i", "z1")
    await reg.register(p2, "Z", "a", "i", "z2")
    assert len(await reg.list_all(p1)) == 1
    assert len(await reg.list_all(p2)) == 1
