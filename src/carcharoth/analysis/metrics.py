"""Pure performance metrics with signed long/short FIFO round trips."""

import bisect
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import pairwise
from math import sqrt
from statistics import fmean, median, pstdev

from carcharoth.domain.models import (
    DecisionRecord,
    EquityPoint,
    MetricValue,
    PositionSnapshot,
    Side,
    TargetExposure,
    TradeRecord,
)

_SESSION_SECONDS = 6.5 * 3600
_TRADING_DAYS_PER_YEAR = 252.0
_TRADING_SECONDS_PER_YEAR = _SESSION_SECONDS * _TRADING_DAYS_PER_YEAR


@dataclass(frozen=True, slots=True)
class RoundTrip:
    symbol: str
    direction: TargetExposure
    qty: float
    entry_price: float
    exit_price: float
    pnl: float
    opened_at: datetime
    closed_at: datetime
    exit_reason: str = ""
    model_id: str = ""
    entry_features: dict[str, float] = field(default_factory=dict)
    exit_features: dict[str, float] = field(default_factory=dict)
    entry_prediction: dict[str, float] = field(default_factory=dict)
    exit_prediction: dict[str, float] = field(default_factory=dict)
    mae_pct: float | None = None
    mfe_pct: float | None = None


@dataclass(slots=True)
class _Lot:
    qty: float
    price: float
    opened_at: datetime
    decision: DecisionRecord | None


def _decision_before(
    records: list[DecisionRecord], timestamps: list[datetime], ts: datetime
) -> DecisionRecord | None:
    """Latest decision strictly before a fill (next-open fills share the next bar timestamp)."""
    index = bisect.bisect_left(timestamps, ts)
    return records[index - 1] if index > 0 else None


def match_round_trips(
    trades: Sequence[TradeRecord],
    decisions: Sequence[DecisionRecord] | None = None,
) -> list[RoundTrip]:
    """FIFO-match both long and short lots; crossing fills close before opening."""
    by_symbol_side: dict[tuple[str, Side], list[DecisionRecord]] = defaultdict(list)
    for decision in sorted(decisions or [], key=lambda item: item.timestamp):
        if decision.order_side is not None:
            by_symbol_side[(decision.symbol, decision.order_side)].append(decision)
    timestamps = {
        key: [decision.timestamp for decision in records] for key, records in by_symbol_side.items()
    }

    longs: dict[str, deque[_Lot]] = defaultdict(deque)
    shorts: dict[str, deque[_Lot]] = defaultdict(deque)
    result: list[RoundTrip] = []
    for trade in sorted(trades, key=lambda item: item.timestamp):
        key = (trade.symbol, trade.side)
        trade_decision = _decision_before(
            by_symbol_side.get(key, []), timestamps.get(key, []), trade.timestamp
        )
        remaining = trade.qty
        closing = shorts[trade.symbol] if trade.side is Side.BUY else longs[trade.symbol]
        direction = TargetExposure.SHORT if trade.side is Side.BUY else TargetExposure.LONG
        while remaining > 0 and closing:
            lot = closing[0]
            matched = min(remaining, lot.qty)
            pnl = (
                (lot.price - trade.price) * matched
                if direction is TargetExposure.SHORT
                else (trade.price - lot.price) * matched
            )
            result.append(
                RoundTrip(
                    symbol=trade.symbol,
                    direction=direction,
                    qty=matched,
                    entry_price=lot.price,
                    exit_price=trade.price,
                    pnl=pnl,
                    opened_at=lot.opened_at,
                    closed_at=trade.timestamp,
                    exit_reason=trade_decision.reason if trade_decision else "",
                    model_id=lot.decision.model_id if lot.decision else "",
                    entry_features=dict(lot.decision.features) if lot.decision else {},
                    exit_features=dict(trade_decision.features) if trade_decision else {},
                    entry_prediction=dict(lot.decision.prediction) if lot.decision else {},
                    exit_prediction=(dict(trade_decision.prediction) if trade_decision else {}),
                )
            )
            lot.qty -= matched
            remaining -= matched
            if lot.qty == 0:
                closing.popleft()
        if remaining > 0:
            opening = longs[trade.symbol] if trade.side is Side.BUY else shorts[trade.symbol]
            opening.append(_Lot(remaining, trade.price, trade.timestamp, trade_decision))
    return result


def enrich_with_excursions(
    round_trips: list[RoundTrip],
    snapshots: Sequence[PositionSnapshot],
) -> list[RoundTrip]:
    by_symbol: dict[str, list[PositionSnapshot]] = defaultdict(list)
    for snapshot in sorted(snapshots, key=lambda item: (item.symbol, item.timestamp)):
        by_symbol[snapshot.symbol].append(snapshot)
    timestamps = {
        symbol: [snapshot.timestamp for snapshot in values] for symbol, values in by_symbol.items()
    }
    result: list[RoundTrip] = []
    for trip in round_trips:
        values = by_symbol.get(trip.symbol, [])
        times = timestamps.get(trip.symbol, [])
        low = bisect.bisect_left(times, trip.opened_at)
        high = bisect.bisect_right(times, trip.closed_at)
        pnl_values = [values[index].unrealized_pnl for index in range(low, high)]
        entry_value = abs(trip.entry_price * trip.qty)
        if not pnl_values or entry_value == 0:
            result.append(trip)
            continue
        result.append(
            replace(
                trip,
                mae_pct=min(min(pnl_values), 0.0) / entry_value,
                mfe_pct=max(max(pnl_values), 0.0) / entry_value,
            )
        )
    return result


def compute_metrics(
    equity: Sequence[EquityPoint],
    trades: Sequence[TradeRecord],
    round_trips: Sequence[RoundTrip] | None = None,
) -> list[MetricValue]:
    metrics: list[MetricValue] = []
    metrics.extend(_equity_metrics(equity))
    if round_trips is None:
        round_trips = match_round_trips(trades)
    metrics.append(MetricValue("num_trades", float(len(round_trips))))
    if round_trips:
        metrics.extend(_trade_metrics(round_trips))
        metrics.extend(_symbol_pnl(round_trips))
    return metrics


def _equity_metrics(equity: Sequence[EquityPoint]) -> list[MetricValue]:
    if len(equity) < 2 or equity[0].equity <= 0:
        return []
    values = [point.equity for point in equity]
    metrics = [
        MetricValue("total_return", values[-1] / values[0] - 1),
        MetricValue("max_drawdown", _max_drawdown(values)),
    ]
    sharpe = _sharpe(equity)
    if sharpe is not None:
        metrics.append(MetricValue("sharpe", sharpe))
    return metrics


def _max_drawdown(values: Sequence[float]) -> float:
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = max(worst, 1 - value / peak)
    return worst


def _sharpe(equity: Sequence[EquityPoint]) -> float | None:
    returns = [
        later.equity / earlier.equity - 1
        for earlier, later in pairwise(equity)
        if earlier.equity > 0
    ]
    if len(returns) < 2:
        return None
    standard_deviation = pstdev(returns)
    if standard_deviation == 0:
        return 0.0
    deltas = [
        (later.timestamp - earlier.timestamp).total_seconds() for earlier, later in pairwise(equity)
    ]
    bar_seconds = float(median(deltas))
    if bar_seconds <= 0:
        return None
    periods_per_year = (
        _TRADING_DAYS_PER_YEAR
        if bar_seconds >= _SESSION_SECONDS
        else _TRADING_SECONDS_PER_YEAR / bar_seconds
    )
    return fmean(returns) / standard_deviation * sqrt(periods_per_year)


def _trade_metrics(round_trips: Sequence[RoundTrip]) -> list[MetricValue]:
    wins = [trip.pnl for trip in round_trips if trip.pnl > 0]
    losses = [trip.pnl for trip in round_trips if trip.pnl < 0]
    metrics = [MetricValue("win_rate", len(wins) / len(round_trips))]
    if wins:
        metrics.append(MetricValue("avg_win", fmean(wins)))
    if losses:
        metrics.append(MetricValue("avg_loss", fmean(losses)))
        metrics.append(MetricValue("profit_factor", sum(wins) / -sum(losses)))
    maes = [trip.mae_pct for trip in round_trips if trip.mae_pct is not None]
    mfes = [trip.mfe_pct for trip in round_trips if trip.mfe_pct is not None]
    if maes:
        metrics.append(MetricValue("avg_mae", fmean(maes)))
    if mfes:
        metrics.append(MetricValue("avg_mfe", fmean(mfes)))
    return metrics


def _symbol_pnl(round_trips: Sequence[RoundTrip]) -> list[MetricValue]:
    pnl_by_symbol: dict[str, float] = defaultdict(float)
    for trip in round_trips:
        pnl_by_symbol[trip.symbol] += trip.pnl
    return [
        MetricValue("symbol_pnl", pnl, symbol=symbol)
        for symbol, pnl in sorted(pnl_by_symbol.items())
    ]
