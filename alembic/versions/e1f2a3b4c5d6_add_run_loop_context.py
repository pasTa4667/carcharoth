"""Add optional research-loop grouping, without changing run UUIDs.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("loop_context", postgresql.JSONB(), nullable=True))
    op.create_index(
        "ix_runs_loop_id",
        "runs",
        [sa.text("(loop_context ->> 'loop_id')")],
        postgresql_where=sa.text("loop_context IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_runs_loop_id", table_name="runs")
    op.drop_column("runs", "loop_context")
