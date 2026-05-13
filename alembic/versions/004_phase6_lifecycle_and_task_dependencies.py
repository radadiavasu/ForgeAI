"""Agent lifecycle log and task dependency_titles (Phase 6).

Revision ID: 004_phase6
Revises: 003_project_artefacts
Create Date: 2026-05-13

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_phase6"
down_revision: Union[str, None] = "003_project_artefacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()

    if "agent_lifecycle_events" not in tables:
        op.create_table(
            "agent_lifecycle_events",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("agent_id", sa.String(length=256), nullable=False),
            sa.Column("agent_role", sa.String(length=64), nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("created_by", sa.String(length=64), nullable=False),
            sa.Column("project_id", sa.Uuid(), nullable=True),
            sa.Column("development_phase", sa.String(length=128), nullable=True),
            sa.Column(
                "timestamp",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_agent_lifecycle_events_agent_id",
            "agent_lifecycle_events",
            ["agent_id"],
            unique=False,
        )
        op.create_index(
            "ix_agent_lifecycle_events_project_id",
            "agent_lifecycle_events",
            ["project_id"],
            unique=False,
        )
        insp = sa.inspect(bind)
        tables = insp.get_table_names()

    cols = {c["name"] for c in insp.get_columns("tasks")} if "tasks" in tables else set()
    if "dependency_titles" not in cols:
        op.add_column(
            "tasks",
            sa.Column(
                "dependency_titles",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()

    if "tasks" in tables:
        cols = {c["name"] for c in insp.get_columns("tasks")}
        if "dependency_titles" in cols:
            op.drop_column("tasks", "dependency_titles")

    if "agent_lifecycle_events" in tables:
        index_names = {ix["name"] for ix in insp.get_indexes("agent_lifecycle_events")}
        if "ix_agent_lifecycle_events_agent_id" in index_names:
            op.drop_index("ix_agent_lifecycle_events_agent_id", table_name="agent_lifecycle_events")
        if "ix_agent_lifecycle_events_project_id" in index_names:
            op.drop_index("ix_agent_lifecycle_events_project_id", table_name="agent_lifecycle_events")
        op.drop_table("agent_lifecycle_events")
