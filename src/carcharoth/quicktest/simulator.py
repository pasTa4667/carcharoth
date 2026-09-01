"""Per-symbol fixed-model simulation with next-bar-open long/short fills."""

import logging
from dataclasses import dataclass
from datetime import datetime

from carcharoth.domain.models import (
    Bar,
    EquityPoint,
    MarketSnapshot,
    Position,
    PositionSnapshot,
    Side,
    TargetExposure,
    TradeRecord,
)
from carcharoth.features.session import is_regular_session
from carcharoth.interfaces.model_signal import ModelSignalGenerator
from carcharoth.quicktest.result import SymbolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SimulationSettings:
    capital: float
    position_size_pct: float
    spread_pct: float = 0.0
    slippage_pct: float = 0.0


@dataclass(slots=True)
class _Holding:
    qty: float
    entry_price: float


def simulate_symbol(
    model: ModelSignalGenerator,
    symbol: str,
    bars: list[Bar],
    benchmark_bars: list[Bar],
    start: datetime,
    end_exclusive: datetime,
    settings: SimulationSettings,
) -> SymbolResult:
    result = SymbolResult(symbol=symbol)
    requirements = model.market_requirements()
    lookback = requirements.bars.lookback
    cash = settings.capital
    holding: _Holding | None = None
    pending: tuple[Side, float] | None = None

    benchmark_regular = sorted(
        [bar for bar in benchmark_bars if is_regular_session(bar.timestamp)],
        key=lambda bar: bar.timestamp,
    )
    for index, bar in enumerate(bars):
        timestamp = bar.timestamp
        if not start <= timestamp < end_exclusive or not is_regular_session(timestamp):
            continue

        if pending is not None:
            side, qty = pending
            fill_price = _fill_price(side, bar.open, settings)
            cash, holding = _fill(symbol, side, qty, fill_price, timestamp, cash, holding, result)
            pending = None

        position = _position(symbol, holding, bar.close) if holding else None
        window = bars[max(0, index + 1 - lookback) : index + 1]
        benchmark_window = [
            candidate for candidate in benchmark_regular if candidate.timestamp <= timestamp
        ][-lookback:]
        context_symbol = next(iter(requirements.context_symbols), symbol)
        snapshot_bars = {symbol: window}
        if context_symbol != symbol:
            snapshot_bars[context_symbol] = benchmark_window
        snapshot = MarketSnapshot(bars=snapshot_bars, quotes={}, as_of=timestamp)
        intent = model.generate(symbol, snapshot, position)
        pending = _plan(intent.target, holding, bar.close, settings)

        if holding is not None:
            result.snapshots.append(
                PositionSnapshot(
                    symbol=symbol,
                    timestamp=timestamp,
                    unrealized_pnl=(bar.close - holding.entry_price) * holding.qty,
                )
            )
        equity = cash + (holding.qty * bar.close if holding else 0.0)
        result.equity.append(EquityPoint(timestamp=timestamp, equity=equity))
        result.cash.append(EquityPoint(timestamp=timestamp, equity=cash))

    if holding is not None:
        logger.info("%s: signed position %.4f remains open at window end", symbol, holding.qty)
    return result


def _plan(
    target: TargetExposure,
    holding: _Holding | None,
    close: float,
    settings: SimulationSettings,
) -> tuple[Side, float] | None:
    if target is TargetExposure.UNCHANGED:
        return None
    if holding is not None:
        current = TargetExposure.LONG if holding.qty > 0 else TargetExposure.SHORT
        if target is current:
            return None
        return (Side.SELL if holding.qty > 0 else Side.BUY, abs(holding.qty))
    if target is TargetExposure.FLAT or close <= 0:
        return None
    qty = settings.capital * settings.position_size_pct / close
    return (Side.BUY if target is TargetExposure.LONG else Side.SELL, qty)


def _fill_price(side: Side, open_price: float, settings: SimulationSettings) -> float:
    if side is Side.BUY:
        return open_price * (1 + settings.spread_pct / 2) * (1 + settings.slippage_pct)
    return open_price * (1 - settings.spread_pct / 2) * (1 - settings.slippage_pct)


def _fill(
    symbol: str,
    side: Side,
    qty: float,
    price: float,
    timestamp: datetime,
    cash: float,
    holding: _Holding | None,
    result: SymbolResult,
) -> tuple[float, _Holding | None]:
    delta = qty if side is Side.BUY else -qty
    cash -= delta * price
    result.trades.append(TradeRecord(symbol, side, qty, price, timestamp))
    if holding is None:
        return cash, _Holding(delta, price)
    new_qty = holding.qty + delta
    if abs(new_qty) < 1e-12:
        return cash, None
    if holding.qty * delta > 0:
        holding.entry_price = (holding.entry_price * abs(holding.qty) + price * abs(delta)) / abs(
            new_qty
        )
    elif holding.qty * new_qty < 0:
        holding.entry_price = price
    holding.qty = new_qty
    return cash, holding


def _position(symbol: str, holding: _Holding, price: float) -> Position:
    return Position(
        symbol=symbol,
        qty=holding.qty,
        avg_entry_price=holding.entry_price,
        market_value=holding.qty * price,
        unrealized_pnl=(price - holding.entry_price) * holding.qty,
        current_price=price,
    )
