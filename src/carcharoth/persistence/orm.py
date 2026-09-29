"""SQLAlchemy 2.0 mapped classes — our persisted history.

Alpaca remains the source of truth for live account state; these tables are
the bot's own audit trail of decisions, orders, fills and snapshots.

Every data row belongs to a run (`runs.run_id`): one row per app start
(run_type PAPER) or backtest (run_type BACKTEST). Deleting a run cascades
to all of its data.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
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
    loop_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))

    __table_args__ = (
        Index(
            "ix_runs_loop_id",
            loop_context["loop_id"].astext,
            postgresql_where=loop_context.is_not(None),
        ),
    )


class BacktestPerformanceRow(Base):
    __tablename__ = "backtest_performance"
    __table_args__ = (Index("ix_backtest_performance_started", "started_at"),)

    execution_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.run_id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str]
    started_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    wall_seconds: Mapped[float] = mapped_column(Float)
    cpu_seconds: Mapped[float] = mapped_column(Float)
    simulation_cpu_seconds: Mapped[float] = mapped_column(Float)
    telemetry_seconds: Mapped[float] = mapped_column(Float)
    periodic_enabled: Mapped[bool]
    publication_errors: Mapped[int]
    counters: Mapped[dict[str, Any]]
    execution_metadata: Mapped[dict[str, Any]]
    recent_ticks_per_second: Mapped[float | None] = mapped_column(Float)


class BacktestPerformanceStageRow(Base):
    __tablename__ = "backtest_performance_stages"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backtest_performance.execution_id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(primary_key=True)
    calls: Mapped[int]
    inclusive_seconds: Mapped[float] = mapped_column(Float)
    exclusive_seconds: Mapped[float] = mapped_column(Float)
    max_seconds: Mapped[float] = mapped_column(Float)


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


class ModelDecisionRow(Base):
    __tablename__ = "model_decisions"
    __table_args__ = (Index("ix_model_decisions_run_ts", "run_id", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    timestamp: Mapped[datetime]
    symbol: Mapped[str]
    model_id: Mapped[str]
    schema_hash: Mapped[str]
    target: Mapped[str]
    reason: Mapped[str] = mapped_column(Text)
    prediction: Mapped[dict[str, Any]] = mapped_column(default=dict)
    features: Mapped[dict[str, Any]] = mapped_column(default=dict)
    risk_approved: Mapped[bool | None]
    risk_reason: Mapped[str | None] = mapped_column(Text)
    risk_qty: Mapped[Decimal | None]
    order_side: Mapped[str | None]


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
    """One closed signed position (FIFO-matched), enriched with model context."""

    __tablename__ = "round_trips"
    __table_args__ = (Index("ix_round_trips_run_entry", "run_id", "entry_time"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    symbol: Mapped[str]
    direction: Mapped[str]
    qty: Mapped[Decimal]
    entry_time: Mapped[datetime]
    exit_time: Mapped[datetime]
    entry_price: Mapped[Decimal]
    exit_price: Mapped[Decimal]
    pnl: Mapped[Decimal]
    holding_seconds: Mapped[int]
    model_id: Mapped[str]
    exit_reason: Mapped[str] = mapped_column(Text)
    entry_features: Mapped[dict[str, Any]] = mapped_column(default=dict)
    exit_features: Mapped[dict[str, Any]] = mapped_column(default=dict)
    entry_prediction: Mapped[dict[str, Any]] = mapped_column(default=dict)
    exit_prediction: Mapped[dict[str, Any]] = mapped_column(default=dict)
    mae_pct: Mapped[Decimal | None]
    mfe_pct: Mapped[Decimal | None]


class PermutationTestRow(Base):
    """One permutation test against a baseline run: method, inputs, and the
    verdict (observed score vs. the permuted-score distribution). This row
    alone answers "did the strategy beat luck". Monte carlo trade-shuffle
    tests report distributions only: their verdict columns are NULL."""

    __tablename__ = "permutation_tests"

    test_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    #: baseline run (run_type QUICKTEST today; any run type later)
    run_id: Mapped[uuid.UUID] = _run_id_column()
    method: Mapped[str]
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    n_permutations: Mapped[int]
    seed: Mapped[int]
    objective: Mapped[str]
    significance: Mapped[float | None]
    observed_score: Mapped[float]
    p_value: Mapped[float | None]
    passed: Mapped[bool | None]
    created_at: Mapped[datetime]


class PermutationResultRow(Base):
    """One permutation's score + headline performance (Grafana histograms).
    total_return/max_drawdown are fractions (0.05 = 5%), like backtest_metrics."""

    __tablename__ = "permutation_results"
    __table_args__ = (UniqueConstraint("test_id", "permutation_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permutation_tests.test_id", ondelete="CASCADE")
    )
    permutation_index: Mapped[int]
    score: Mapped[float]
    total_return: Mapped[float | None]
    max_drawdown: Mapped[float | None]
    sharpe: Mapped[float | None]
    profit_factor: Mapped[float | None]
    num_round_trips: Mapped[int]
    final_equity: Mapped[float | None]


class DataEligibilityAssessmentRow(Base):
    """Assessments exist independently of successful model artifacts."""

    __tablename__ = "data_eligibility_assessments"

    assessment_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    scope: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(index=True)
    updated_at: Mapped[datetime]
    config_hash: Mapped[str]
    status: Mapped[str]
    model_id: Mapped[str | None] = mapped_column(index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), index=True
    )
    report: Mapped[dict[str, Any]]


class ModelDiagnosticsRow(Base):
    """Bounded training diagnostics for one immutable model artifact."""

    __tablename__ = "model_diagnostics"

    model_id: Mapped[str] = mapped_column(primary_key=True)
    task: Mapped[str]
    validation_start: Mapped[date] = mapped_column(Date)
    validation_end: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime]
    report: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ConfigurationRow(Base):
    __tablename__ = "configurations"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
