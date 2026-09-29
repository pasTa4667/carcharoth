"""Persist data eligibility independently of successful model artifacts.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_eligibility_assessments",
        sa.Column("assessment_id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.run_id", ondelete="CASCADE")),
        sa.Column("report", postgresql.JSONB(), nullable=False),
    )
    for column in ("created_at", "model_id", "run_id"):
        op.create_index(
            f"ix_data_eligibility_assessments_{column}", "data_eligibility_assessments", [column]
        )


def downgrade() -> None:
    op.drop_table("data_eligibility_assessments")
