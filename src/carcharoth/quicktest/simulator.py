"""Per-symbol signed-position strategy simulation without engine or risk I/O."""

import logging
from dataclasses import dataclass
from datetime import datetime

from carcharoth.domain.models import (
    Bar,
    EquityPoint,
    Position,
    PositionSnapshot,
    Side,
    TradeIntent,
    TradeRecord,
)
from carcharoth.interfaces.strategy import Strategy
from carcharoth.quicktest.result import SymbolResult
from carcharoth.strategies.session import minutes_since_open, minutes_until_close

logger = logging.getLogger(__name__)
_SECONDS_PER_YEAR = 365.0 * 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class SimulationSettings:
    capital: float
    position_size_pct: float
    spread_pct: float = 0.0
    slippage_pct: float = 0.0
    margin_leverage: float = 1.0
    short_borrow_apr: float = 0.0


@dataclass(slots=True)
class _Holding:
    qty: float  # signed
    entry_price: float
    short_fees: float = 0.0


def simulate_symbol(
    strategy: Strategy,
    symbol: str,
    bars: list[Bar],
    start: datetime,
    end_exclusive: datetime,
    settings: SimulationSettings,
) -> SymbolResult:
    result = SymbolResult(symbol=symbol)
    lookback = strategy.required_bars().lookback
    cash = settings.capital
    holding: _Holding | None = None
    previous_ts: datetime | None = None
    previous_price: float | None = None

    for index, bar in enumerate(bars):
        ts = bar.timestamp
        if ts < start or ts >= end_exclusive:
            continue
        if minutes_since_open(ts) < 0 or minutes_until_close(ts) <= 0:
            continue

        if holding is not None and holding.qty < 0 and previous_ts is not None:
            elapsed = max(0.0, (ts - previous_ts).total_seconds())
            fee = (
                abs(holding.qty)
                * (previous_price if previous_price is not None else holding.entry_price)
                * settings.short_borrow_apr
                * elapsed
                / _SECONDS_PER_YEAR
            )
            cash -= fee
            holding.short_fees += fee

        window = bars[max(0, index + 1 - lookback) : index + 1]
        position = _position(symbol, holding, bar.close) if holding else None
        signal = strategy.evaluate(symbol, window, None, position)
        intent = signal.action

        if intent in (TradeIntent.OPEN_LONG, TradeIntent.OPEN_SHORT) and holding is None:
            side = intent.order_side
            assert side is not None
            fill_price = _fill_price(bar.close, side, settings)
            buying_power = _buying_power(cash, holding, bar.close, settings.margin_leverage)
            notional = min(settings.capital * settings.position_size_pct, buying_power)
            qty = notional / fill_price if fill_price > 0 else 0.0
            if qty > 0:
                signed_qty = qty if intent is TradeIntent.OPEN_LONG else -qty
                cash -= signed_qty * fill_price
                holding = _Holding(qty=signed_qty, entry_price=fill_price)
                result.trades.append(
                    TradeRecord(
                        symbol,
                        side,
                        qty,
                        fill_price,
                        ts,
                        intent=intent,
                    )
                )
        elif intent is TradeIntent.CLOSE_LONG and holding is not None and holding.qty > 0:
            fill_price = _fill_price(bar.close, Side.SELL, settings)
            qty = holding.qty
            cash += fill_price * qty
            result.trades.append(TradeRecord(symbol, Side.SELL, qty, fill_price, ts, intent=intent))
            holding = None
        elif intent is TradeIntent.CLOSE_SHORT and holding is not None and holding.qty < 0:
            fill_price = _fill_price(bar.close, Side.BUY, settings)
            qty = abs(holding.qty)
            cash -= fill_price * qty
            result.trades.append(
                TradeRecord(
                    symbol,
                    Side.BUY,
                    qty,
                    fill_price,
                    ts,
                    intent=intent,
                    fees=holding.short_fees,
                )
            )
            holding = None

        if holding is not None:
            result.snapshots.append(
                PositionSnapshot(
                    symbol=symbol,
                    timestamp=ts,
                    unrealized_pnl=(bar.close - holding.entry_price) * holding.qty,
                )
            )
        equity = cash + (holding.qty * bar.close if holding else 0.0)
        result.equity.append(EquityPoint(timestamp=ts, equity=equity))
        result.cash.append(EquityPoint(timestamp=ts, equity=cash))
        previous_ts = ts
        previous_price = bar.close

    if holding is not None:
        logger.info(
            "%s: %s position of %.4f shares still open at end of window",
            symbol,
            "long" if holding.qty > 0 else "short",
            abs(holding.qty),
        )
    return result


def _fill_price(close: float, side: Side, settings: SimulationSettings) -> float:
    if side is Side.BUY:
        return close * (1 + settings.spread_pct / 2) * (1 + settings.slippage_pct)
    return close * (1 - settings.spread_pct / 2) * (1 - settings.slippage_pct)


def _buying_power(
    cash: float, holding: _Holding | None, price: float, margin_leverage: float
) -> float:
    signed_value = holding.qty * price if holding else 0.0
    equity = cash + signed_value
    gross = abs(signed_value)
    return max(0.0, equity * margin_leverage - gross)


def _position(symbol: str, holding: _Holding, price: float) -> Position:
    return Position(
        symbol=symbol,
        qty=holding.qty,
        avg_entry_price=holding.entry_price,
        market_value=holding.qty * price,
        unrealized_pnl=(price - holding.entry_price) * holding.qty,
        current_price=price,
    )
