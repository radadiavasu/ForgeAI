"""Task and TaskStateHistory ORM models."""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from forgeai.state_machine.states import TaskState


class Base(DeclarativeBase):
    """Declarative base for ForgeAI models."""

    pass


class TaskComplexity(str, PyEnum):
    """Relative effort estimate for a task."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Task(Base):
    """A unit of work moving through the task state machine."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_agent: Mapped[str] = mapped_column(String(256), nullable=False)
    complexity: Mapped[TaskComplexity] = mapped_column(
        SAEnum(
            TaskComplexity,
            values_callable=lambda m: [e.value for e in m],
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    current_state: Mapped[TaskState] = mapped_column(
        SAEnum(
            TaskState,
            values_callable=lambda m: [e.value for e in m],
            native_enum=False,
            length=32,
        ),
        nullable=False,
        default=TaskState.PHASE_LOCKED,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    dependency_titles: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
    )

    history: Mapped[list["TaskStateHistory"]] = relationship(
        "TaskStateHistory",
        back_populates="task",
        order_by="TaskStateHistory.attempted_at",
    )


class TaskStateHistory(Base):
    """Audit log row for every transition attempt (success or failure)."""

    __tablename__ = "task_state_history"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[str] = mapped_column(String(256), nullable=False)
    from_state: Mapped[TaskState] = mapped_column(
        SAEnum(
            TaskState,
            values_callable=lambda m: [e.value for e in m],
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    to_state: Mapped[TaskState] = mapped_column(
        SAEnum(
            TaskState,
            values_callable=lambda m: [e.value for e in m],
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    defect_report: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    task: Mapped["Task"] = relationship("Task", back_populates="history")
