"""SQLAlchemy 2.0 mapped classes — our persisted history.

Alpaca remains the source of truth for live account state; these tables are
the bot's own audit trail of decisions, orders, fills and snapshots.

Every data row belongs to a run (`runs.run_id`): one row per app start
(run_type PAPER) or backtest (run_type BACKTEST). Deleting a run cascades
to all of its data.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MONEY = Numeric(18, 6)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {
        datetime: DateTime(timezone=True),
        Decimal: MONEY,
        dict[str, Any]: JSONB,
        uuid.UUID: Uuid(),
    }


def _run_id_column() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"))


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    run_type: Mapped[str]  # "PAPER" | "BACKTEST"
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    symbols: Mapped[list[str]] = mapped_column(JSONB, default=list)
    backtest_start: Mapped[datetime | None]
    backtest_end: Mapped[datetime | None]
    note: Mapped[str | None] = mapped_column(Text)


class TradeRow(Base):
    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_run_ts", "run_id", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    broker_order_id: Mapped[str] = mapped_column(unique=True)
    symbol: Mapped[str]
    side: Mapped[str]
    qty: Mapped[Decimal]
    price: Mapped[Decimal]
    fees: Mapped[Decimal] = mapped_column(default=Decimal(0))
    timestamp: Mapped[datetime]


class PositionSnapshotRow(Base):
    __tablename__ = "positions_snapshot"
    __table_args__ = (
        Index("ix_positions_snapshot_ts_symbol", "timestamp", "symbol"),
        Index("ix_positions_snapshot_run_ts", "run_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    timestamp: Mapped[datetime]
    symbol: Mapped[str]
    qty: Mapped[Decimal]
    avg_price: Mapped[Decimal]
    market_value: Mapped[Decimal]
    unrealized_pnl: Mapped[Decimal]


class StrategyDecisionRow(Base):
    __tablename__ = "strategy_decisions"
    __table_args__ = (Index("ix_strategy_decisions_run_ts", "run_id", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    timestamp: Mapped[datetime]
    symbol: Mapped[str]
    strategy: Mapped[str]
    signal: Mapped[str]
    reason: Mapped[str] = mapped_column(Text)
    indicators: Mapped[dict[str, Any]] = mapped_column(default=dict)
    risk_approved: Mapped[bool | None]
    risk_reason: Mapped[str | None] = mapped_column(Text)
    risk_qty: Mapped[Decimal | None]


class OrderRow(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_run_status", "run_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    broker_order_id: Mapped[str] = mapped_column(unique=True)
    client_order_id: Mapped[str | None]
    symbol: Mapped[str]
    side: Mapped[str]
    qty: Mapped[Decimal]
    status: Mapped[str]
    created_at: Mapped[datetime]
    filled_at: Mapped[datetime | None]
    filled_avg_price: Mapped[Decimal | None]


class RegimeEvaluationRow(Base):
    __tablename__ = "regime_evaluations"
    __table_args__ = (
        Index("ix_regime_evaluations_symbol_ts", "symbol", "timestamp"),
        Index("ix_regime_evaluations_run_ts", "run_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    timestamp: Mapped[datetime]
    symbol: Mapped[str]
    regime: Mapped[str]
    score: Mapped[float]
    directional_score: Mapped[float]
    stability: Mapped[float]
    features: Mapped[dict[str, Any]] = mapped_column(default=dict)
    #: full regime posterior for probabilistic detectors (HMM); NULL otherwise
    #: (none_as_null: Python None -> SQL NULL, not JSON 'null')
    probabilities: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )


class StrategyAssignmentRow(Base):
    """Append-only history of which strategy trades a symbol; the newest row
    per symbol is the current assignment (DISTINCT ON (symbol) ... ORDER BY
    since DESC)."""

    __tablename__ = "strategy_assignments"
    __table_args__ = (
        Index("ix_strategy_assignments_symbol_since", "symbol", "since"),
        Index("ix_strategy_assignments_run_symbol", "run_id", "symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    symbol: Mapped[str]
    strategy: Mapped[str]
    regime: Mapped[str]
    since: Mapped[datetime]


class EquitySnapshotRow(Base):
    """Total account value per tick — the portfolio equity curve of a run."""

    __tablename__ = "equity_snapshots"
    __table_args__ = (Index("ix_equity_snapshots_run_ts", "run_id", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    timestamp: Mapped[datetime]
    equity: Mapped[Decimal]
    cash: Mapped[Decimal]
    buying_power: Mapped[Decimal]


class BacktestMetricRow(Base):
    """Analyzer output: one metric value per row. Portfolio-level metrics have
    symbol NULL; per-symbol metrics (e.g. symbol_pnl) set it."""

    __tablename__ = "backtest_metrics"
    __table_args__ = (UniqueConstraint("run_id", "name", "symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    name: Mapped[str]
    symbol: Mapped[str | None]
    value: Mapped[Decimal]


class RoundTripRow(Base):
    """One closed long position (FIFO-matched), enriched with strategy context."""

    __tablename__ = "round_trips"
    __table_args__ = (Index("ix_round_trips_run_entry", "run_id", "entry_time"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    symbol: Mapped[str]
    qty: Mapped[Decimal]
    entry_time: Mapped[datetime]
    exit_time: Mapped[datetime]
    entry_price: Mapped[Decimal]
    exit_price: Mapped[Decimal]
    pnl: Mapped[Decimal]
    holding_seconds: Mapped[int]
    strategy: Mapped[str]
    exit_reason: Mapped[str] = mapped_column(Text)
    regime: Mapped[str | None]
    entry_indicators: Mapped[dict[str, Any]] = mapped_column(default=dict)
    exit_indicators: Mapped[dict[str, Any]] = mapped_column(default=dict)
    mae_pct: Mapped[Decimal | None]
    mfe_pct: Mapped[Decimal | None]


class ConfigurationRow(Base):
    __tablename__ = "configurations"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
