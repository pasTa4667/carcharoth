"""add bounded backtest performance snapshots

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_performance",
        sa.Column("execution_id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.run_id", ondelete="SET NULL")),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("wall_seconds", sa.Float(), nullable=False),
        sa.Column("cpu_seconds", sa.Float(), nullable=False),
        sa.Column("simulation_cpu_seconds", sa.Float(), nullable=False),
        sa.Column("telemetry_seconds", sa.Float(), nullable=False),
        sa.Column("periodic_enabled", sa.Boolean(), nullable=False),
        sa.Column("publication_errors", sa.Integer(), nullable=False),
        sa.Column("counters", postgresql.JSONB(), nullable=False),
        sa.Column("execution_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("recent_ticks_per_second", sa.Float()),
    )
    op.create_index("ix_backtest_performance_started", "backtest_performance", ["started_at"])
    op.create_index("ix_backtest_performance_run_id", "backtest_performance", ["run_id"])
    op.create_table(
        "backtest_performance_stages",
        sa.Column(
            "execution_id",
            sa.Uuid(),
            sa.ForeignKey("backtest_performance.execution_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("path", sa.String(), primary_key=True),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("inclusive_seconds", sa.Float(), nullable=False),
        sa.Column("exclusive_seconds", sa.Float(), nullable=False),
        sa.Column("max_seconds", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("backtest_performance_stages")
    op.drop_index("ix_backtest_performance_run_id", table_name="backtest_performance")
    op.drop_index("ix_backtest_performance_started", table_name="backtest_performance")
    op.drop_table("backtest_performance")
