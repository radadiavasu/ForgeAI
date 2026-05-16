"""Persist change history to Project_Memory (Phase 9B)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from forgeai.lifecycle.schemas import ChangeHistoryEntry

if TYPE_CHECKING:
    from forgeai.agents.lead_agent import LeadAgent


async def write_change_history(lead: LeadAgent, entry: ChangeHistoryEntry) -> str:
    """Store a change history entry as a versioned project artefact."""
    pid = uuid.UUID(entry.project_id)
    await lead.persist_versioned_artefact(
        pid,
        f"change_history:{entry.entry_id}",
        entry.model_dump(mode="json"),
        created_by="lead_agent",
    )
    return entry.entry_id
