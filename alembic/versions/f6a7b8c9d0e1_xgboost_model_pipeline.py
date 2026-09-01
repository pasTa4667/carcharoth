"""replace strategy audit schema with model decisions and signed trips

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("strategy_decisions", "model_decisions")
    op.execute("ALTER INDEX ix_strategy_decisions_run_ts RENAME TO ix_model_decisions_run_ts")
    op.alter_column("model_decisions", "strategy", new_column_name="model_id")
    op.alter_column("model_decisions", "signal", new_column_name="target")
    op.alter_column("model_decisions", "indicators", new_column_name="features")
    op.add_column(
        "model_decisions",
        sa.Column("schema_hash", sa.String(), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "model_decisions",
        sa.Column(
            "prediction",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("model_decisions", sa.Column("order_side", sa.String(), nullable=True))
    op.execute("UPDATE model_decisions SET model_id = 'legacy:' || model_id")
    op.execute(
        "UPDATE model_decisions SET target = CASE target "
        "WHEN 'buy' THEN 'long' WHEN 'sell' THEN 'flat' ELSE 'unchanged' END"
    )
    op.execute(
        "UPDATE model_decisions SET order_side = CASE target "
        "WHEN 'long' THEN 'buy' WHEN 'flat' THEN 'sell' ELSE NULL END"
    )
    op.alter_column("model_decisions", "schema_hash", server_default=None)
    op.alter_column("model_decisions", "prediction", server_default=None)

    op.add_column(
        "round_trips",
        sa.Column("direction", sa.String(), nullable=False, server_default="long"),
    )
    op.alter_column("round_trips", "strategy", new_column_name="model_id")
    op.alter_column("round_trips", "entry_indicators", new_column_name="entry_features")
    op.alter_column("round_trips", "exit_indicators", new_column_name="exit_features")
    op.add_column(
        "round_trips",
        sa.Column(
            "entry_prediction",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "round_trips",
        sa.Column(
            "exit_prediction",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute("UPDATE round_trips SET model_id = 'legacy:' || model_id")
    op.alter_column("round_trips", "direction", server_default=None)
    op.alter_column("round_trips", "entry_prediction", server_default=None)
    op.alter_column("round_trips", "exit_prediction", server_default=None)
    op.drop_column("round_trips", "regime")

    op.drop_table("strategy_assignments")


def downgrade() -> None:
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

    op.add_column("round_trips", sa.Column("regime", sa.String(), nullable=True))
    op.drop_column("round_trips", "exit_prediction")
    op.drop_column("round_trips", "entry_prediction")
    op.alter_column("round_trips", "exit_features", new_column_name="exit_indicators")
    op.alter_column("round_trips", "entry_features", new_column_name="entry_indicators")
    op.execute("UPDATE round_trips SET model_id = replace(model_id, 'legacy:', '')")
    op.alter_column("round_trips", "model_id", new_column_name="strategy")
    op.drop_column("round_trips", "direction")

    op.execute(
        "UPDATE model_decisions SET target = CASE target "
        "WHEN 'long' THEN 'buy' WHEN 'flat' THEN 'sell' ELSE 'hold' END"
    )
    op.execute("UPDATE model_decisions SET model_id = replace(model_id, 'legacy:', '')")
    op.drop_column("model_decisions", "order_side")
    op.drop_column("model_decisions", "prediction")
    op.drop_column("model_decisions", "schema_hash")
    op.alter_column("model_decisions", "features", new_column_name="indicators")
    op.alter_column("model_decisions", "target", new_column_name="signal")
    op.alter_column("model_decisions", "model_id", new_column_name="strategy")
    op.execute("ALTER INDEX ix_model_decisions_run_ts RENAME TO ix_strategy_decisions_run_ts")
    op.rename_table("model_decisions", "strategy_decisions")
