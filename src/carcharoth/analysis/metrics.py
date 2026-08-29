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
    PositionDirection,
    PositionSnapshot,
    Side,
    TradeIntent,
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
    direction: PositionDirection = PositionDirection.LONG
    fees: float = 0.0
    exit_reason: str = ""
    strategy: str = ""
    regime: str | None = None
    entry_indicators: dict[str, float] = field(default_factory=dict)
    exit_indicators: dict[str, float] = field(default_factory=dict)
    #: worst unrealized loss as a fraction of entry value (negative or zero)
    mae_pct: float | None = None
    #: best unrealized gain as a fraction of entry value (positive or zero)
    mfe_pct: float | None = None


@dataclass(slots=True)
class _Lot:
    qty: float
    price: float
    opened_at: datetime
    fee_per_share: float = 0.0


def _find_earlier[T](records: list[T], timestamps: list[datetime], ts: datetime) -> T | None:
    """Return the latest record whose timestamp/since is ≤ ts, or None."""
    idx = bisect.bisect_right(timestamps, ts)
    return records[idx - 1] if idx > 0 else None


def match_round_trips(
    trades: Sequence[TradeRecord],
    decisions: Sequence[DecisionRecord] | None = None,
    assignments: Sequence[AssignmentRecord] | None = None,
) -> list[RoundTrip]:
    """FIFO-match explicit long and short opens/closes per symbol."""
    decisions_by_key: dict[tuple[str, TradeIntent], list[DecisionRecord]] = defaultdict(list)
    if decisions:
        for decision in sorted(decisions, key=lambda item: item.timestamp):
            decisions_by_key[(decision.symbol, decision.action)].append(decision)
    decision_ts = {
        key: [decision.timestamp for decision in records]
        for key, records in decisions_by_key.items()
    }

    asgn_by_sym: dict[str, list[AssignmentRecord]] = defaultdict(list)
    if assignments:
        for assignment in sorted(assignments, key=lambda item: item.since):
            asgn_by_sym[assignment.symbol].append(assignment)
    asgn_ts = {symbol: [item.since for item in records] for symbol, records in asgn_by_sym.items()}

    lots: dict[tuple[str, PositionDirection], deque[_Lot]] = defaultdict(deque)
    round_trips: list[RoundTrip] = []
    for trade in trades:
        intent = trade.intent or (
            TradeIntent.OPEN_LONG if trade.side is Side.BUY else TradeIntent.CLOSE_LONG
        )
        direction = intent.direction
        if direction is None:
            continue
        key = (trade.symbol, direction)
        if intent.opens_position:
            fee_per_share = trade.fees / trade.qty if trade.qty > 0 else 0.0
            lots[key].append(_Lot(trade.qty, trade.price, trade.timestamp, fee_per_share))
            continue
        if not intent.closes_position:
            continue

        remaining = trade.qty
        open_lots = lots[key]
        exit_fee_per_share = trade.fees / trade.qty if trade.qty > 0 else 0.0
        exit_decisions = decisions_by_key.get((trade.symbol, intent), [])
        exit_decision = _find_earlier(
            exit_decisions,
            decision_ts.get((trade.symbol, intent), []),
            trade.timestamp,
        )
        open_intent = (
            TradeIntent.OPEN_LONG if direction is PositionDirection.LONG else TradeIntent.OPEN_SHORT
        )
        while remaining > 0 and open_lots:
            lot = open_lots[0]
            matched = min(remaining, lot.qty)
            entry_decisions = decisions_by_key.get((trade.symbol, open_intent), [])
            entry_decision = _find_earlier(
                entry_decisions,
                decision_ts.get((trade.symbol, open_intent), []),
                lot.opened_at,
            )
            matched_assignment = _find_earlier(
                asgn_by_sym.get(trade.symbol, []),
                asgn_ts.get(trade.symbol, []),
                lot.opened_at,
            )
            gross_pnl = (
                (trade.price - lot.price) * matched
                if direction is PositionDirection.LONG
                else (lot.price - trade.price) * matched
            )
            fees = (lot.fee_per_share + exit_fee_per_share) * matched
            round_trips.append(
                RoundTrip(
                    symbol=trade.symbol,
                    qty=matched,
                    entry_price=lot.price,
                    exit_price=trade.price,
                    pnl=gross_pnl - fees,
                    opened_at=lot.opened_at,
                    closed_at=trade.timestamp,
                    direction=direction,
                    fees=fees,
                    exit_reason=exit_decision.reason if exit_decision else "",
                    strategy=entry_decision.strategy if entry_decision else "",
                    regime=matched_assignment.regime if matched_assignment else None,
                    entry_indicators=(dict(entry_decision.indicators) if entry_decision else {}),
                    exit_indicators=dict(exit_decision.indicators) if exit_decision else {},
                )
            )
            lot.qty -= matched
            remaining -= matched
            if lot.qty <= 0:
                open_lots.popleft()
    return round_trips


def enrich_with_excursions(
    round_trips: list[RoundTrip],
    snapshots: Sequence[PositionSnapshot],
) -> list[RoundTrip]:
    """Return copies of round_trips enriched with mae_pct and mfe_pct.

    Snapshots are bar-close unrealized P&L readings. For each round trip we
    find all snapshots within [opened_at, closed_at] for the same symbol, then
    express the worst (MAE) and best (MFE) reading as a fraction of entry value.
    Trips with no matching snapshots keep mae_pct/mfe_pct = None.
    """
    by_symbol: dict[str, list[PositionSnapshot]] = defaultdict(list)
    for snap in sorted(snapshots, key=lambda s: (s.symbol, s.timestamp)):
        by_symbol[snap.symbol].append(snap)
    sym_ts: dict[str, list[datetime]] = {
        sym: [s.timestamp for s in snaps] for sym, snaps in by_symbol.items()
    }

    result: list[RoundTrip] = []
    for trip in round_trips:
        snaps = by_symbol.get(trip.symbol, [])
        timestamps = sym_ts.get(trip.symbol, [])
        lo = bisect.bisect_left(timestamps, trip.opened_at)
        hi = bisect.bisect_right(timestamps, trip.closed_at)
        window = [snaps[i].unrealized_pnl for i in range(lo, hi)]
        if not window:
            result.append(trip)
            continue
        entry_value = abs(trip.entry_price * trip.qty)
        if entry_value == 0:
            result.append(trip)
            continue
        mae_pct = min(min(window), 0.0) / entry_value
        mfe_pct = max(max(window), 0.0) / entry_value
        result.append(
            RoundTrip(
                symbol=trip.symbol,
                qty=trip.qty,
                entry_price=trip.entry_price,
                exit_price=trip.exit_price,
                pnl=trip.pnl,
                opened_at=trip.opened_at,
                closed_at=trip.closed_at,
                direction=trip.direction,
                fees=trip.fees,
                exit_reason=trip.exit_reason,
                strategy=trip.strategy,
                regime=trip.regime,
                entry_indicators=trip.entry_indicators,
                exit_indicators=trip.exit_indicators,
                mae_pct=mae_pct,
                mfe_pct=mfe_pct,
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
        metrics.extend(_direction_metrics(round_trips))
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
    maes = [trip.mae_pct for trip in round_trips if trip.mae_pct is not None]
    mfes = [trip.mfe_pct for trip in round_trips if trip.mfe_pct is not None]
    if maes:
        metrics.append(MetricValue("avg_mae", fmean(maes)))
    if mfes:
        metrics.append(MetricValue("avg_mfe", fmean(mfes)))
    return metrics


def _direction_metrics(round_trips: Sequence[RoundTrip]) -> list[MetricValue]:
    metrics: list[MetricValue] = []
    for direction in PositionDirection:
        trips = [trip for trip in round_trips if trip.direction is direction]
        if not trips:
            continue
        prefix = direction.value
        wins = [trip.pnl for trip in trips if trip.pnl > 0]
        losses = [trip.pnl for trip in trips if trip.pnl < 0]
        metrics.extend(
            [
                MetricValue(f"{prefix}_num_trades", float(len(trips))),
                MetricValue(f"{prefix}_pnl", sum(trip.pnl for trip in trips)),
                MetricValue(f"{prefix}_win_rate", len(wins) / len(trips)),
            ]
        )
        if losses:
            metrics.append(MetricValue(f"{prefix}_profit_factor", sum(wins) / -sum(losses)))
    return metrics


def _symbol_pnl(round_trips: Sequence[RoundTrip]) -> list[MetricValue]:
    pnl_by_symbol: dict[str, float] = defaultdict(float)
    for trip in round_trips:
        pnl_by_symbol[trip.symbol] += trip.pnl
    return [
        MetricValue("symbol_pnl", pnl, symbol=symbol)
        for symbol, pnl in sorted(pnl_by_symbol.items())
    ]
