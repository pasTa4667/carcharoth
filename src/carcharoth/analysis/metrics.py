"""Pure performance-metric computations — no I/O, fully unit-testable.

Trade metrics are based on realized round trips (FIFO lot matching); open
positions at the end of a run contribute to the equity curve metrics but
not to win rate / profit factor.
"""

import bisect
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from math import sqrt
from statistics import fmean, median, pstdev

from carcharoth.domain.models import (
    AssignmentRecord,
    DecisionRecord,
    EquityPoint,
    MetricValue,
    Side,
    TradeRecord,
)

#: One NYSE session in seconds (6.5 hours); used to annualize bar returns.
_SESSION_SECONDS = 6.5 * 3600
_TRADING_DAYS_PER_YEAR = 252.0
_TRADING_SECONDS_PER_YEAR = _SESSION_SECONDS * _TRADING_DAYS_PER_YEAR


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """A closed long position (or the FIFO-matched part of one)."""

    symbol: str
    qty: float
    entry_price: float
    exit_price: float
    pnl: float
    opened_at: datetime
    closed_at: datetime
    exit_reason: str = ""
    strategy: str = ""
    regime: str | None = None
    entry_indicators: dict[str, float] = field(default_factory=dict)
    exit_indicators: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class _Lot:
    qty: float
    price: float
    opened_at: datetime


def _find_earlier[T](records: list[T], timestamps: list[datetime], ts: datetime) -> T | None:
    """Return the latest record whose timestamp/since is ≤ ts, or None."""
    idx = bisect.bisect_right(timestamps, ts)
    return records[idx - 1] if idx > 0 else None


def match_round_trips(
    trades: Sequence[TradeRecord],
    decisions: Sequence[DecisionRecord] | None = None,
    assignments: Sequence[AssignmentRecord] | None = None,
) -> list[RoundTrip]:
    """FIFO-match sells against buys per symbol. Sells without a matching
    open lot (e.g. positions predating the run) are skipped.

    When decisions and assignments are provided, each round trip is enriched
    with the nearest-earlier strategy decision indicators and the regime that
    was active when the position was opened.
    """
    # Build per-symbol decision lookups (sorted by timestamp).
    buy_decs: dict[str, list[DecisionRecord]] = defaultdict(list)
    sell_decs: dict[str, list[DecisionRecord]] = defaultdict(list)
    if decisions:
        for d in sorted(decisions, key=lambda d: d.timestamp):
            if d.side is Side.BUY:
                buy_decs[d.symbol].append(d)
            elif d.side is Side.SELL:
                sell_decs[d.symbol].append(d)
    buy_ts = {s: [d.timestamp for d in ds] for s, ds in buy_decs.items()}
    sell_ts = {s: [d.timestamp for d in ds] for s, ds in sell_decs.items()}

    # Build per-symbol assignment lookup (sorted by since).
    asgn_by_sym: dict[str, list[AssignmentRecord]] = defaultdict(list)
    if assignments:
        for a in sorted(assignments, key=lambda a: a.since):
            asgn_by_sym[a.symbol].append(a)
    asgn_ts = {s: [a.since for a in al] for s, al in asgn_by_sym.items()}

    lots: dict[str, deque[_Lot]] = defaultdict(deque)
    round_trips: list[RoundTrip] = []
    for trade in trades:
        if trade.side is Side.BUY:
            lots[trade.symbol].append(_Lot(trade.qty, trade.price, trade.timestamp))
            continue
        remaining = trade.qty
        open_lots = lots[trade.symbol]
        sym = trade.symbol
        sell_dec = _find_earlier(sell_decs.get(sym, []), sell_ts.get(sym, []), trade.timestamp)
        while remaining > 0 and open_lots:
            lot = open_lots[0]
            matched = min(remaining, lot.qty)
            buy_dec = _find_earlier(buy_decs.get(sym, []), buy_ts.get(sym, []), lot.opened_at)
            asgn = _find_earlier(asgn_by_sym.get(sym, []), asgn_ts.get(sym, []), lot.opened_at)
            round_trips.append(
                RoundTrip(
                    symbol=trade.symbol,
                    qty=matched,
                    entry_price=lot.price,
                    exit_price=trade.price,
                    pnl=(trade.price - lot.price) * matched,
                    opened_at=lot.opened_at,
                    closed_at=trade.timestamp,
                    exit_reason=sell_dec.reason if sell_dec else "",
                    strategy=buy_dec.strategy if buy_dec else "",
                    regime=asgn.regime if asgn else None,
                    entry_indicators=dict(buy_dec.indicators) if buy_dec else {},
                    exit_indicators=dict(sell_dec.indicators) if sell_dec else {},
                )
            )
            lot.qty -= matched
            remaining -= matched
            if lot.qty == 0:
                open_lots.popleft()
    return round_trips


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
    """Largest peak-to-trough loss as a positive fraction of the peak."""
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = max(worst, 1 - value / peak)
    return worst


def _sharpe(equity: Sequence[EquityPoint]) -> float | None:
    """Annualized Sharpe (risk-free rate 0) from per-bar equity returns.
    Bar spacing is inferred from the median timestamp delta; anything of a
    session or longer counts as daily."""
    returns = [
        later.equity / earlier.equity - 1
        for earlier, later in pairwise(equity)
        if earlier.equity > 0
    ]
    if len(returns) < 2:
        return None
    std = pstdev(returns)
    if std == 0:
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
    return fmean(returns) / std * sqrt(periods_per_year)


def _trade_metrics(round_trips: Sequence[RoundTrip]) -> list[MetricValue]:
    wins = [trip.pnl for trip in round_trips if trip.pnl > 0]
    losses = [trip.pnl for trip in round_trips if trip.pnl < 0]
    metrics = [MetricValue("win_rate", len(wins) / len(round_trips))]
    if wins:
        metrics.append(MetricValue("avg_win", fmean(wins)))
    if losses:
        metrics.append(MetricValue("avg_loss", fmean(losses)))
        metrics.append(MetricValue("profit_factor", sum(wins) / -sum(losses)))
    return metrics


def _symbol_pnl(round_trips: Sequence[RoundTrip]) -> list[MetricValue]:
    pnl_by_symbol: dict[str, float] = defaultdict(float)
    for trip in round_trips:
        pnl_by_symbol[trip.symbol] += trip.pnl
    return [
        MetricValue("symbol_pnl", pnl, symbol=symbol)
        for symbol, pnl in sorted(pnl_by_symbol.items())
    ]
