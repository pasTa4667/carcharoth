"""SQLAlchemy 2.0 mapped classes — our persisted history.

Alpaca remains the source of truth for live account state; these tables are
the bot's own audit trail of decisions, orders, fills and snapshots.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import DateTime, Index, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MONEY = Numeric(18, 6)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {
        datetime: DateTime(timezone=True),
        Decimal: MONEY,
        dict[str, Any]: JSONB,
    }


class TradeRow(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_order_id: Mapped[str] = mapped_column(unique=True)
    symbol: Mapped[str]
    side: Mapped[str]
    qty: Mapped[Decimal]
    price: Mapped[Decimal]
    fees: Mapped[Decimal] = mapped_column(default=Decimal(0))
    timestamp: Mapped[datetime]


class PositionSnapshotRow(Base):
    __tablename__ = "positions_snapshot"
    __table_args__ = (Index("ix_positions_snapshot_ts_symbol", "timestamp", "symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime]
    symbol: Mapped[str]
    qty: Mapped[Decimal]
    avg_price: Mapped[Decimal]
    market_value: Mapped[Decimal]
    unrealized_pnl: Mapped[Decimal]


class StrategyDecisionRow(Base):
    __tablename__ = "strategy_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
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

    id: Mapped[int] = mapped_column(primary_key=True)
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
    __table_args__ = (Index("ix_regime_evaluations_symbol_ts", "symbol", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime]
    symbol: Mapped[str]
    regime: Mapped[str]
    score: Mapped[float]
    directional_score: Mapped[float]
    stability: Mapped[float]
    features: Mapped[dict[str, Any]] = mapped_column(default=dict)


class StrategyAssignmentRow(Base):
    """Append-only history of which strategy trades a symbol; the newest row
    per symbol is the current assignment (DISTINCT ON (symbol) ... ORDER BY
    since DESC)."""

    __tablename__ = "strategy_assignments"
    __table_args__ = (Index("ix_strategy_assignments_symbol_since", "symbol", "since"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str]
    strategy: Mapped[str]
    regime: Mapped[str]
    since: Mapped[datetime]


class ConfigurationRow(Base):
    __tablename__ = "configurations"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
