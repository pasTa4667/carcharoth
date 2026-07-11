"""add run tracking, equity snapshots and backtest metrics

Revision ID: c3f1a9d27b5e
Revises: a65840aff339
Create Date: 2026-07-09 18:00:00.000000

Every data row now belongs to a run (PAPER app start or BACKTEST). Existing
rows predate run tracking and are backfilled onto a synthetic legacy run.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f1a9d27b5e"
down_revision: str | Sequence[str] | None = "a65840aff339"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_RUN_ID = "00000000-0000-0000-0000-000000000001"

#: table -> (index name, indexed columns)
_RUN_SCOPED_TABLES: dict[str, tuple[str, list[str]]] = {
    "trades": ("ix_trades_run_ts", ["run_id", "timestamp"]),
    "orders": ("ix_orders_run_status", ["run_id", "status"]),
    "positions_snapshot": ("ix_positions_snapshot_run_ts", ["run_id", "timestamp"]),
    "strategy_decisions": ("ix_strategy_decisions_run_ts", ["run_id", "timestamp"]),
    "regime_evaluations": ("ix_regime_evaluations_run_ts", ["run_id", "timestamp"]),
    "strategy_assignments": ("ix_strategy_assignments_run_symbol", ["run_id", "symbol"]),
}


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("run_type", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("symbols", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("backtest_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backtest_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("run_id"),
    )

    op.execute(
        f"""
        INSERT INTO runs (run_id, run_type, started_at, config, symbols, note)
        VALUES ('{LEGACY_RUN_ID}', 'PAPER', now(), '{{}}'::jsonb, '[]'::jsonb,
                'legacy pre-run-tracking data')
        """
    )

    for table, (index_name, index_columns) in _RUN_SCOPED_TABLES.items():
        op.add_column(table, sa.Column("run_id", sa.Uuid(), nullable=True))
        op.execute(f"UPDATE {table} SET run_id = '{LEGACY_RUN_ID}'")
        op.alter_column(table, "run_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_run_id_runs", table, "runs", ["run_id"], ["run_id"], ondelete="CASCADE"
        )
        op.create_index(index_name, table, index_columns, unique=False)

    op.create_table(
        "equity_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("cash", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("buying_power", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_equity_snapshots_run_ts", "equity_snapshots", ["run_id", "timestamp"], unique=False
    )

    op.create_table(
        "backtest_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=True),
        sa.Column("value", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "name", "symbol"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("backtest_metrics")
    op.drop_index("ix_equity_snapshots_run_ts", table_name="equity_snapshots")
    op.drop_table("equity_snapshots")
    for table, (index_name, _) in reversed(_RUN_SCOPED_TABLES.items()):
        op.drop_index(index_name, table_name=table)
        op.drop_constraint(f"fk_{table}_run_id_runs", table, type_="foreignkey")
        op.drop_column(table, "run_id")
    op.drop_table("runs")
