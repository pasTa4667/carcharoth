"""reconcile XGBoost schema drift from reused f6a7b8c9d0e1 revision

Alembic recorded f6a7b8c9d0e1 as applied when that revision still added
long/short intent columns. The current f6a7b8c9d0e1 migration renames
strategy_decisions to model_decisions and reshapes round_trips, but those
steps never ran on drifted databases.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-31 00:00:01.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def _has_table(table: str) -> bool:
    inspector = inspect(op.get_bind())
    return table in inspector.get_table_names()


def _reconcile_model_decisions() -> None:
    if _has_table("strategy_decisions") and not _has_table("model_decisions"):
        op.rename_table("strategy_decisions", "model_decisions")
        op.execute(
            "ALTER INDEX IF EXISTS ix_strategy_decisions_run_ts "
            "RENAME TO ix_model_decisions_run_ts"
        )

    if not _has_table("model_decisions"):
        return

    columns = _columns("model_decisions")
    if "strategy" in columns:
        op.alter_column("model_decisions", "strategy", new_column_name="model_id")
    if "signal" in columns:
        op.alter_column("model_decisions", "signal", new_column_name="target")
    if "indicators" in columns:
        op.alter_column("model_decisions", "indicators", new_column_name="features")

    columns = _columns("model_decisions")
    if "schema_hash" not in columns:
        op.add_column(
            "model_decisions",
            sa.Column("schema_hash", sa.String(), nullable=False, server_default="legacy"),
        )
    if "prediction" not in columns:
        op.add_column(
            "model_decisions",
            sa.Column(
                "prediction",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )
    if "order_side" not in columns:
        op.add_column("model_decisions", sa.Column("order_side", sa.String(), nullable=True))

    op.execute(
        "UPDATE model_decisions SET model_id = 'legacy:' || model_id "
        "WHERE model_id NOT LIKE 'legacy:%'"
    )
    op.execute(
        "UPDATE model_decisions SET target = CASE target "
        "WHEN 'buy' THEN 'long' WHEN 'sell' THEN 'flat' ELSE 'unchanged' END "
        "WHERE target IN ('buy', 'sell', 'hold')"
    )
    op.execute(
        "UPDATE model_decisions SET order_side = CASE target "
        "WHEN 'long' THEN 'buy' WHEN 'flat' THEN 'sell' ELSE NULL END "
        "WHERE order_side IS NULL"
    )

    if "schema_hash" in _columns("model_decisions"):
        op.alter_column("model_decisions", "schema_hash", server_default=None)
    if "prediction" in _columns("model_decisions"):
        op.alter_column("model_decisions", "prediction", server_default=None)


def _reconcile_round_trips() -> None:
    if not _has_table("round_trips"):
        return

    columns = _columns("round_trips")
    if "direction" not in columns:
        op.add_column(
            "round_trips",
            sa.Column("direction", sa.String(), nullable=False, server_default="long"),
        )

    columns = _columns("round_trips")
    if "strategy" in columns:
        op.alter_column("round_trips", "strategy", new_column_name="model_id")
    if "entry_indicators" in columns:
        op.alter_column("round_trips", "entry_indicators", new_column_name="entry_features")
    if "exit_indicators" in columns:
        op.alter_column("round_trips", "exit_indicators", new_column_name="exit_features")

    columns = _columns("round_trips")
    if "entry_prediction" not in columns:
        op.add_column(
            "round_trips",
            sa.Column(
                "entry_prediction",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )
    if "exit_prediction" not in columns:
        op.add_column(
            "round_trips",
            sa.Column(
                "exit_prediction",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )

    op.execute(
        "UPDATE round_trips SET model_id = 'legacy:' || model_id "
        "WHERE model_id NOT LIKE 'legacy:%'"
    )

    columns = _columns("round_trips")
    if "regime" in columns:
        op.drop_column("round_trips", "regime")
    if "fees" in columns:
        op.drop_column("round_trips", "fees")

    if "direction" in _columns("round_trips"):
        op.alter_column("round_trips", "direction", server_default=None)
    if "entry_prediction" in _columns("round_trips"):
        op.alter_column("round_trips", "entry_prediction", server_default=None)
    if "exit_prediction" in _columns("round_trips"):
        op.alter_column("round_trips", "exit_prediction", server_default=None)


def _drop_strategy_assignments() -> None:
    if not _has_table("strategy_assignments"):
        return
    op.execute("DROP INDEX IF EXISTS ix_strategy_assignments_symbol_since")
    op.execute("DROP INDEX IF EXISTS ix_strategy_assignments_run_symbol")
    op.drop_table("strategy_assignments")


def upgrade() -> None:
    """Upgrade schema."""
    _reconcile_model_decisions()
    _reconcile_round_trips()
    _drop_strategy_assignments()


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "strategy_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.run_id", ondelete="CASCADE")),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("strategy", sa.String(), nullable=False),
        sa.Column("regime", sa.String(), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_strategy_assignments_symbol_since", "strategy_assignments", ["symbol", "since"]
    )
    op.create_index(
        "ix_strategy_assignments_run_symbol", "strategy_assignments", ["run_id", "symbol"]
    )

    if not _has_table("round_trips"):
        return

    columns = _columns("round_trips")
    if "fees" not in columns:
        op.add_column(
            "round_trips",
            sa.Column("fees", sa.Numeric(precision=18, scale=6), nullable=False, server_default="0"),
        )
        op.alter_column("round_trips", "fees", server_default=None)
    if "regime" not in columns:
        op.add_column("round_trips", sa.Column("regime", sa.String(), nullable=True))

    if "exit_prediction" in _columns("round_trips"):
        op.drop_column("round_trips", "exit_prediction")
    if "entry_prediction" in _columns("round_trips"):
        op.drop_column("round_trips", "entry_prediction")
    if "exit_features" in _columns("round_trips"):
        op.alter_column("round_trips", "exit_features", new_column_name="exit_indicators")
    if "entry_features" in _columns("round_trips"):
        op.alter_column("round_trips", "entry_features", new_column_name="entry_indicators")
    if "model_id" in _columns("round_trips"):
        op.execute("UPDATE round_trips SET model_id = replace(model_id, 'legacy:', '')")
        op.alter_column("round_trips", "model_id", new_column_name="strategy")
    if "direction" in _columns("round_trips"):
        op.drop_column("round_trips", "direction")

    if not _has_table("model_decisions"):
        return

    op.execute(
        "UPDATE model_decisions SET target = CASE target "
        "WHEN 'long' THEN 'buy' WHEN 'flat' THEN 'sell' ELSE 'hold' END"
    )
    op.execute("UPDATE model_decisions SET model_id = replace(model_id, 'legacy:', '')")
    if "order_side" in _columns("model_decisions"):
        op.drop_column("model_decisions", "order_side")
    if "prediction" in _columns("model_decisions"):
        op.drop_column("model_decisions", "prediction")
    if "schema_hash" in _columns("model_decisions"):
        op.drop_column("model_decisions", "schema_hash")
    if "features" in _columns("model_decisions"):
        op.alter_column("model_decisions", "features", new_column_name="indicators")
    if "target" in _columns("model_decisions"):
        op.alter_column("model_decisions", "target", new_column_name="signal")
    if "model_id" in _columns("model_decisions"):
        op.alter_column("model_decisions", "model_id", new_column_name="strategy")
    op.execute(
        "ALTER INDEX IF EXISTS ix_model_decisions_run_ts "
        "RENAME TO ix_strategy_decisions_run_ts"
    )
    op.rename_table("model_decisions", "strategy_decisions")
