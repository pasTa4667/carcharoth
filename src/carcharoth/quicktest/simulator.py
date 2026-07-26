"""Per-symbol in-memory strategy simulation — no engine, no risk manager.

One symbol at a time: walk its regular-session bars, keep a rolling lookback
window, call ``strategy.evaluate()``, and fill BUY/SELL signals immediately
at that bar's close adjusted by spread/slippage (same fill model as
``SimulatedBroker``). BUY sizing is ``capital * position_size_pct`` notional;
one open position per symbol (a BUY while long is ignored, a SELL closes the
whole position) — matching the engine's behavior.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from carcharoth.domain.models import (
    Bar,
    EquityPoint,
    Position,
    PositionSnapshot,
    Side,
    SignalAction,
    TradeRecord,
)
from carcharoth.interfaces.strategy import Strategy
from carcharoth.quicktest.result import SymbolResult
from carcharoth.strategies.session import minutes_since_open, minutes_until_close

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SimulationSettings:
    """Fill and sizing parameters of one quick-test run."""

    capital: float
    position_size_pct: float
    spread_pct: float = 0.0
    slippage_pct: float = 0.0


@dataclass(slots=True)
class _Holding:
    qty: float
    entry_price: float


def simulate_symbol(
    strategy: Strategy,
    symbol: str,
    bars: list[Bar],
    start: datetime,
    end_exclusive: datetime,
    settings: SimulationSettings,
) -> SymbolResult:
    """Run one independent symbol simulation over [start, end_exclusive).

    ``bars`` must include warm-up history before ``start`` (oldest first);
    the strategy is only evaluated on regular-session bars inside the window,
    but the rolling window it sees reaches back into the warm-up.
    """
    result = SymbolResult(symbol=symbol)
    lookback = strategy.required_bars().lookback
    cash = settings.capital
    holding: _Holding | None = None

    for index, bar in enumerate(bars):
        ts = bar.timestamp
        if ts < start or ts >= end_exclusive:
            continue
        # Alpaca minute bars include pre-/after-hours; like the backtest
        # runner, only regular-session bars are replayed.
        if minutes_since_open(ts) < 0 or minutes_until_close(ts) <= 0:
            continue

        window = bars[max(0, index + 1 - lookback) : index + 1]
        position = _position(symbol, holding, bar.close) if holding else None
        signal = strategy.evaluate(symbol, window, None, position)

        if signal.action is SignalAction.BUY and holding is None:
            fill_price = bar.close * (1 + settings.spread_pct / 2) * (1 + settings.slippage_pct)
            notional = min(settings.capital * settings.position_size_pct, cash)
            qty = notional / fill_price
            if qty > 0:
                cash -= fill_price * qty
                holding = _Holding(qty=qty, entry_price=fill_price)
                result.trades.append(TradeRecord(symbol, Side.BUY, qty, fill_price, ts))
        elif signal.action is SignalAction.SELL and holding is not None:
            fill_price = bar.close * (1 - settings.spread_pct / 2) * (1 - settings.slippage_pct)
            cash += fill_price * holding.qty
            result.trades.append(TradeRecord(symbol, Side.SELL, holding.qty, fill_price, ts))
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

    if holding is not None:
        logger.info(
            "%s: position of %.4f shares still open at end of window (counted in equity, "
            "not in trade metrics)",
            symbol,
            holding.qty,
        )
    return result


def _position(symbol: str, holding: _Holding, price: float) -> Position:
    return Position(
        symbol=symbol,
        qty=holding.qty,
        avg_entry_price=holding.entry_price,
        market_value=holding.qty * price,
        unrealized_pnl=(price - holding.entry_price) * holding.qty,
        current_price=price,
    )
