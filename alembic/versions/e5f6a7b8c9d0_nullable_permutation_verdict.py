"""make permutation_tests verdict columns nullable

Monte carlo trade-shuffle tests report distributions only — no significance
threshold, no p-value, no PASS/FAIL — so the verdict columns become nullable
and are stored as NULL for those tests.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("permutation_tests", "significance", existing_type=sa.Float(), nullable=True)
    op.alter_column("permutation_tests", "p_value", existing_type=sa.Float(), nullable=True)
    op.alter_column("permutation_tests", "passed", existing_type=sa.Boolean(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # NULL verdicts (monte carlo tests) cannot survive a NOT NULL downgrade.
    op.execute("DELETE FROM permutation_tests WHERE p_value IS NULL")
    op.alter_column("permutation_tests", "significance", existing_type=sa.Float(), nullable=False)
    op.alter_column("permutation_tests", "p_value", existing_type=sa.Float(), nullable=False)
    op.alter_column("permutation_tests", "passed", existing_type=sa.Boolean(), nullable=False)
