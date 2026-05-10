"""escalation_events table

Revision ID: 002_escalation
Revises: 001_initial
Create Date: 2026-05-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_escalation"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create table unless it already exists (e.g. ORM ``create_all`` or a prior partial run)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "escalation_events" in insp.get_table_names():
        return

    op.create_table(
        "escalation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.String(length=256), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("error_signature", sa.String(length=512), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=False),
        sa.Column("loop_count", sa.Integer(), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column(
            "needs_human_input",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("human_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_escalation_events_task_id",
        "escalation_events",
        ["task_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "escalation_events" not in insp.get_table_names():
        return
    index_names = {ix["name"] for ix in insp.get_indexes("escalation_events")}
    if "ix_escalation_events_task_id" in index_names:
        op.drop_index("ix_escalation_events_task_id", table_name="escalation_events")
    op.drop_table("escalation_events")
