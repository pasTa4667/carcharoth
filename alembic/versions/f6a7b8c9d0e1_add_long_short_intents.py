"""add long/short intent and direction audit fields

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("intent", sa.String(), nullable=True))
    op.add_column("trades", sa.Column("intent", sa.String(), nullable=True))
    op.execute(
        "UPDATE orders SET intent = CASE side WHEN 'buy' THEN 'open_long' ELSE 'close_long' END"
    )
    op.execute(
        "UPDATE trades SET intent = CASE side WHEN 'buy' THEN 'open_long' ELSE 'close_long' END"
    )
    op.alter_column("orders", "intent", nullable=False)
    op.alter_column("trades", "intent", nullable=False)

    op.add_column(
        "round_trips",
        sa.Column("direction", sa.String(), nullable=False, server_default="long"),
    )
    op.add_column(
        "round_trips",
        sa.Column("fees", sa.Numeric(precision=18, scale=6), nullable=False, server_default="0"),
    )
    op.alter_column("round_trips", "direction", server_default=None)
    op.alter_column("round_trips", "fees", server_default=None)


def downgrade() -> None:
    op.drop_column("round_trips", "fees")
    op.drop_column("round_trips", "direction")
    op.drop_column("trades", "intent")
    op.drop_column("orders", "intent")
