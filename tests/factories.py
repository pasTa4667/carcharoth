"""Helpers to build domain test data."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from carcharoth.domain.models import AccountState, Bar, Position, Quote

BASE_TIME = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)


def make_bars(
    prices: Sequence[float],
    symbol: str = "AAPL",
    hl_range: float = 0.0,
    volumes: Sequence[float] | None = None,
    start: datetime = BASE_TIME,
) -> list[Bar]:
    if volumes is not None and len(volumes) != len(prices):
        raise ValueError("volumes must match prices in length")
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(minutes=5 * i),
            open=price,
            high=price + hl_range / 2,
            low=price - hl_range / 2,
            close=price,
            volume=volumes[i] if volumes is not None else 1000,
        )
        for i, price in enumerate(prices)
    ]


def make_quote(price: float, symbol: str = "AAPL", spread: float = 0.02) -> Quote:
    return Quote(
        symbol=symbol,
        timestamp=BASE_TIME,
        bid_price=price - spread / 2,
        ask_price=price + spread / 2,
        bid_size=100,
        ask_size=100,
    )


def make_position(
    symbol: str = "AAPL", qty: float = 10, price: float = 100.0, market_value: float | None = None
) -> Position:
    return Position(
        symbol=symbol,
        qty=qty,
        avg_entry_price=price,
        market_value=market_value if market_value is not None else qty * price,
        unrealized_pnl=0.0,
        current_price=price,
    )


def make_account(
    equity: float = 100_000.0,
    cash: float | None = None,
    buying_power: float | None = None,
    last_equity: float | None = None,
    positions: dict[str, Position] | None = None,
) -> AccountState:
    return AccountState(
        equity=equity,
        cash=cash if cash is not None else equity,
        buying_power=buying_power if buying_power is not None else equity,
        last_equity=last_equity if last_equity is not None else equity,
        positions=positions or {},
    )
