"""add permutation_tests and permutation_results tables

Revision ID: d4e5f6a7b8c9
Revises: b2a1c3d4e5f6
Create Date: 2026-07-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "b2a1c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "permutation_tests",
        sa.Column("test_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("n_permutations", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("objective", sa.String(), nullable=False),
        sa.Column("significance", sa.Float(), nullable=False),
        sa.Column("observed_score", sa.Float(), nullable=False),
        sa.Column("p_value", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("test_id"),
    )
    op.create_table(
        "permutation_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("test_id", sa.Uuid(), nullable=False),
        sa.Column("permutation_index", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("total_return", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("sharpe", sa.Float(), nullable=True),
        sa.Column("profit_factor", sa.Float(), nullable=True),
        sa.Column("num_round_trips", sa.Integer(), nullable=False),
        sa.Column("final_equity", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["test_id"], ["permutation_tests.test_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("test_id", "permutation_index"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("permutation_results")
    op.drop_table("permutation_tests")
