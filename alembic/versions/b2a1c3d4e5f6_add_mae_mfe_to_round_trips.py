"""add mae_pct and mfe_pct to round_trips

Revision ID: b2a1c3d4e5f6
Revises: 7fdd4f8f4bb3
Create Date: 2026-07-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2a1c3d4e5f6"
down_revision: str | Sequence[str] | None = "34ebb36c4a14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("round_trips", sa.Column("mae_pct", sa.Numeric(precision=18, scale=6), nullable=True))
    op.add_column("round_trips", sa.Column("mfe_pct", sa.Numeric(precision=18, scale=6), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("round_trips", "mfe_pct")
    op.drop_column("round_trips", "mae_pct")
