"""Domain models shared by all components.

These are the only types passed between services; no component may leak
provider SDK types (e.g. alpaca-py models) outside its own package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class SignalAction(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


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
    action: SignalAction
    strategy: str
    reason: str
    indicators: dict[str, float] = field(default_factory=dict)
    timestamp: datetime | None = None


@dataclass(frozen=True, slots=True)
class RiskDecision:
    signal: Signal
    approved: bool
    qty: float
    reason: str


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    side: Side
    qty: float
    order_type: Literal["market"] = "market"
    time_in_force: Literal["day"] = "day"
    client_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class OpenOrder:
    """A not-yet-terminal order as recorded locally; used to detect conflicts
    before submitting a new order for the same symbol."""

    broker_order_id: str
    symbol: str
    side: Side


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
