"""add model-scoped training diagnostics

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_diagnostics",
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("task", sa.String(), nullable=False),
        sa.Column("validation_start", sa.Date(), nullable=False),
        sa.Column("validation_end", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("model_id"),
    )


def downgrade() -> None:
    op.drop_table("model_diagnostics")
