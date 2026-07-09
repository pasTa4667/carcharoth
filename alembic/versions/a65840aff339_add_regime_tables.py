"""add regime tables

Revision ID: a65840aff339
Revises: 09bf5049fb3f
Create Date: 2026-07-09 13:48:38.054007

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a65840aff339"
down_revision: str | Sequence[str] | None = "09bf5049fb3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "regime_evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("regime", sa.String(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("directional_score", sa.Float(), nullable=False),
        sa.Column("stability", sa.Float(), nullable=False),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_regime_evaluations_symbol_ts",
        "regime_evaluations",
        ["symbol", "timestamp"],
        unique=False,
    )
    op.create_table(
        "strategy_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("strategy", sa.String(), nullable=False),
        sa.Column("regime", sa.String(), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_strategy_assignments_symbol_since",
        "strategy_assignments",
        ["symbol", "since"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_strategy_assignments_symbol_since", table_name="strategy_assignments")
    op.drop_table("strategy_assignments")
    op.drop_index("ix_regime_evaluations_symbol_ts", table_name="regime_evaluations")
    op.drop_table("regime_evaluations")
