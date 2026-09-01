"""drop legacy intent columns from orders and trades

The long/short strategy refactor replaced explicit order intents with
TargetExposure on model_decisions and side-only orders/trades. Databases
migrated before the XGBoost schema rewrite may still carry NOT NULL
intent columns that the current ORM no longer writes.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS intent")
    op.execute("ALTER TABLE trades DROP COLUMN IF EXISTS intent")


def downgrade() -> None:
    """Downgrade schema."""
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
