"""Escalation event persistence model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from forgeai.models.task import Base


class EscalationEventModel(Base):
    """PostgreSQL row for ``EscalationEvent`` audit trail."""

    __tablename__ = "escalation_events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(256), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    error_signature: Mapped[str] = mapped_column(String(512), nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, nullable=False)
    loop_count: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_human_input: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    human_message: Mapped[str | None] = mapped_column(Text, nullable=True)
