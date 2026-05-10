"""project_artefacts table for versioned master and stack documents.

Revision ID: 003_project_artefacts
Revises: 002_escalation
Create Date: 2026-05-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_project_artefacts"
down_revision: Union[str, None] = "002_escalation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "project_artefacts" in insp.get_table_names():
        return

    op.create_table(
        "project_artefacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("artefact_type", sa.String(length=64), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_project_artefacts_project_id",
        "project_artefacts",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "project_artefacts" not in insp.get_table_names():
        return
    index_names = {ix["name"] for ix in insp.get_indexes("project_artefacts")}
    if "ix_project_artefacts_project_id" in index_names:
        op.drop_index("ix_project_artefacts_project_id", table_name="project_artefacts")
    op.drop_table("project_artefacts")
