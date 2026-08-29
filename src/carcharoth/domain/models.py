"""Domain models shared by all components.

These are the only types passed between services; no component may leak
provider SDK types (e.g. alpaca-py models) outside its own package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class RunType(StrEnum):
    """What produced a run's data: a live paper-trading session, a backtest,
    or an isolated strategy quick test."""

    PAPER = "PAPER"
    BACKTEST = "BACKTEST"
    QUICKTEST = "QUICKTEST"


class PositionDirection(StrEnum):
    LONG = "long"
    SHORT = "short"


class TradeIntent(StrEnum):
    """Unambiguous trading intent, independent of the broker order side."""

    OPEN_LONG = "open_long"
    CLOSE_LONG = "close_long"
    OPEN_SHORT = "open_short"
    CLOSE_SHORT = "close_short"
    HOLD = "hold"

    # Legacy source aliases for callers/tests written before explicit intent;
    # their serialized values remain the unambiguous forms above.
    BUY = OPEN_LONG
    SELL = CLOSE_LONG

    @classmethod
    def from_stored(cls, value: str) -> TradeIntent:
        """Read current intent values and legacy long-only BUY/SELL rows."""
        if value == "buy":
            return cls.OPEN_LONG
        if value == "sell":
            return cls.CLOSE_LONG
        return cls(value)

    @property
    def order_side(self) -> Side | None:
        if self in (TradeIntent.OPEN_LONG, TradeIntent.CLOSE_SHORT):
            return Side.BUY
        if self in (TradeIntent.OPEN_SHORT, TradeIntent.CLOSE_LONG):
            return Side.SELL
        return None

    @property
    def direction(self) -> PositionDirection | None:
        if self in (TradeIntent.OPEN_LONG, TradeIntent.CLOSE_LONG):
            return PositionDirection.LONG
        if self in (TradeIntent.OPEN_SHORT, TradeIntent.CLOSE_SHORT):
            return PositionDirection.SHORT
        return None

    @property
    def opens_position(self) -> bool:
        return self in (TradeIntent.OPEN_LONG, TradeIntent.OPEN_SHORT)

    @property
    def closes_position(self) -> bool:
        return self in (TradeIntent.CLOSE_LONG, TradeIntent.CLOSE_SHORT)


# Backwards-compatible import name; the values now describe intent rather
# than merely a BUY/SELL broker side.
SignalAction = TradeIntent


class OrderStatus(StrEnum):
    NEW = "new"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


#: Statuses after which an order will never fill (further).
TERMINAL_ORDER_STATUSES = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
)


class TimeframeUnit(StrEnum):
    MINUTE = "minute"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class Timeframe:
    """Bar resolution. Daily bars are a distinct unit because providers
    aggregate them by trading session, not by fixed-minute windows."""

    amount: int
    unit: TimeframeUnit

    def __post_init__(self) -> None:
        if self.amount < 1:
            raise ValueError("timeframe amount must be >= 1")
        if self.unit is TimeframeUnit.DAY and self.amount != 1:
            raise ValueError("daily timeframe must have amount 1")

    @classmethod
    def minutes(cls, amount: int) -> Timeframe:
        return cls(amount, TimeframeUnit.MINUTE)

    @classmethod
    def daily(cls) -> Timeframe:
        return cls(1, TimeframeUnit.DAY)


@dataclass(frozen=True, slots=True)
class BarSpec:
    """A strategy's bar-data requirement: resolution plus how many bars."""

    timeframe: Timeframe
    lookback: int

    def __post_init__(self) -> None:
        if self.lookback < 1:
            raise ValueError("lookback must be >= 1")


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    timestamp: datetime
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float

    @property
    def mid(self) -> float:
        return (self.bid_price + self.ask_price) / 2


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Market data for one engine tick: bars (newest last) and latest quotes per symbol."""

    bars: dict[str, list[Bar]]
    quotes: dict[str, Quote]
    as_of: datetime


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pnl: float
    current_price: float

    @property
    def direction(self) -> PositionDirection:
        if self.qty > 0:
            return PositionDirection.LONG
        if self.qty < 0:
            return PositionDirection.SHORT
        raise ValueError("an open position cannot have zero quantity")

    @property
    def abs_qty(self) -> float:
        return abs(self.qty)


@dataclass(frozen=True, slots=True)
class AccountState:
    equity: float
    cash: float
    buying_power: float
    last_equity: float
    positions: dict[str, Position]
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    action: TradeIntent
    strategy: str
    reason: str
    indicators: dict[str, float] = field(default_factory=dict)
    timestamp: datetime | None = None


@dataclass(frozen=True, slots=True)
class EntryCandidate:
    """One strategy family's comparable opportunity for a direction.

    Scores are heuristic strengths in [0, 1]. The arbiter treats invalid
    values as ineligible so malformed strategy output can never place an order.
    """

    direction: PositionDirection
    score: float
    reason: str
    indicators: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    signal: Signal
    approved: bool
    qty: float
    reason: str


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    intent: TradeIntent
    qty: float
    order_type: Literal["market"] = "market"
    time_in_force: Literal["day"] = "day"
    client_order_id: str | None = None

    @property
    def side(self) -> Side:
        side = self.intent.order_side
        if side is None:
            raise ValueError("a HOLD intent cannot become an order request")
        return side


@dataclass(frozen=True, slots=True)
class OpenOrder:
    """A not-yet-terminal order as recorded locally; used to detect conflicts
    before submitting a new order for the same symbol."""

    broker_order_id: str
    symbol: str
    side: Side


@dataclass(frozen=True, slots=True)
class RunInfo:
    """Metadata of one run (a live session or a backtest)."""

    run_id: UUID
    run_type: RunType
    started_at: datetime
    finished_at: datetime | None
    symbols: list[str]
    backtest_start: datetime | None = None
    backtest_end: datetime | None = None
    #: the effective configuration the run started with (JSON dump of AppConfig)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """A persisted fill, as read back for analysis."""

    symbol: str
    side: Side
    qty: float
    price: float
    timestamp: datetime
    intent: TradeIntent | None = None
    fees: float = 0.0


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """A strategy decision row, as read back for round-trip enrichment."""

    symbol: str
    action: TradeIntent
    timestamp: datetime
    reason: str
    indicators: dict[str, float]
    strategy: str


@dataclass(frozen=True, slots=True)
class AssignmentRecord:
    """A strategy assignment row, as read back for regime lookup."""

    symbol: str
    since: datetime
    regime: str
    strategy: str


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One point of a run's equity curve."""

    timestamp: datetime
    equity: float


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Unrealized P&L for one open position at one bar close — used to compute
    per-trade MAE/MFE during post-run analysis."""

    symbol: str
    timestamp: datetime
    unrealized_pnl: float


@dataclass(frozen=True, slots=True)
class MetricValue:
    """One analyzer result; symbol is set for per-symbol metrics only."""

    name: str
    value: float
    symbol: str | None = None


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Outcome of one backtest run: its id plus the analyzer's metrics."""

    run_id: UUID
    metrics: list[MetricValue]


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """Outcome of one optimization study; best_* fields are None when no
    trial completed."""

    study_name: str
    best_trial_number: int | None
    best_score: float | None
    best_params: dict[str, Any]
    best_run_id: UUID | None
    n_complete: int
    n_failed: int
    n_infeasible: int


@dataclass(frozen=True, slots=True)
class OrderResult:
    broker_order_id: str
    client_order_id: str | None
    symbol: str
    side: Side
    qty: float
    status: OrderStatus
    filled_qty: float
    filled_avg_price: float | None
    submitted_at: datetime
    filled_at: datetime | None
    fees: float = 0.0
