"""Leakage-safe dataset construction with auditable row rejection counts."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from itertools import pairwise

import numpy as np
import numpy.typing as npt

from carcharoth.config.app_config import (
    ClassificationTargetConfig,
    RegressionTargetConfig,
    TargetConfig,
)
from carcharoth.domain.models import Bar, ModelTask
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.features.session import (
    MARKET_TZ,
    TradingSession,
    is_regular_session,
    scheduled_bars,
)
from carcharoth.modeling.eligibility import WindowEvidence

CLASS_SHORT = 0
CLASS_FLAT = 1
CLASS_LONG = 2


@dataclass(frozen=True, slots=True)
class DatasetRow:
    symbol: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class ModelDataset:
    features: npt.NDArray[np.float64]
    labels: npt.NDArray[np.float64] | npt.NDArray[np.int64]
    feature_names: tuple[str, ...]
    rows: tuple[DatasetRow, ...]
    task: ModelTask
    attempted_rows: int
    evidence: dict[str, WindowEvidence] = field(default_factory=dict)

    @property
    def complete_rows(self) -> int:
        return int(self.features.shape[0])

    def select_symbols(self, symbols: Sequence[str]) -> ModelDataset:
        selected = set(symbols)
        keep = np.asarray([row.symbol in selected for row in self.rows], dtype=bool)
        evidence = {s: e for s, e in self.evidence.items() if s in selected}
        return replace(
            self,
            features=self.features[keep],
            labels=self.labels[keep],
            rows=tuple(row for row in self.rows if row.symbol in selected),
            attempted_rows=sum(e.attempted_rows for e in evidence.values()),
            evidence=evidence,
        )


@dataclass(frozen=True, slots=True)
class PreparedSymbol:
    bars: tuple[Bar, ...]
    matrix: npt.NDArray[np.float64]
    errors: tuple[tuple[datetime, str], ...]


def _clean(
    symbol: str, values: Sequence[Bar], grid: Mapping[datetime, date] | None
) -> tuple[list[Bar], tuple[tuple[datetime, str], ...]]:
    rows: dict[datetime, Bar] = {}
    errors: list[tuple[datetime, str]] = []
    for bar in values:
        ts = bar.timestamp
        if ts.tzinfo is None or ts.utcoffset() is None or bar.symbol != symbol:
            raise ValueError("bars require aware timestamps and matching symbols")
        if (grid is not None and ts not in grid) or not is_regular_session(ts):
            continue
        prices = (bar.open, bar.high, bar.low, bar.close)
        if (
            not all(math.isfinite(p) and p > 0 for p in prices)
            or not math.isfinite(bar.volume)
            or bar.volume < 0
            or bar.high < max(prices)
            or bar.low > min(prices)
        ):
            errors.append((ts, "invalid_ohlcv"))
            continue
        previous = rows.get(ts)
        if previous is not None and previous != bar:
            errors.append((ts, "conflicting_duplicate"))
        else:
            rows[ts] = bar
    return sorted(rows.values(), key=lambda bar: bar.timestamp), tuple(errors)


def prepare_dataset(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    symbols: Sequence[str],
    pipeline: FeaturePipeline,
    sessions: Sequence[TradingSession] | None = None,
) -> dict[str, PreparedSymbol]:
    """Compute each requested symbol's feature matrix once for all splits."""
    grid = (
        scheduled_bars(sessions, pipeline.required_bars().timeframe.amount)
        if sessions is not None
        else None
    )
    clean = {
        symbol: _clean(symbol, bars_by_symbol.get(symbol, []), grid)
        for symbol in dict.fromkeys([*symbols, pipeline.benchmark_symbol])
    }
    benchmark = clean[pipeline.benchmark_symbol][0]
    result = {}
    for symbol in symbols:
        values, errors = clean[symbol]
        bars, matrix = pipeline.compute_matrix(values, benchmark)
        result[symbol] = PreparedSymbol(bars, matrix, errors)
    return result


def window_evidence(
    prepared: PreparedSymbol,
    start: datetime,
    end_exclusive: datetime,
    timeframe_minutes: int,
    horizon: int,
    sessions: Sequence[TradingSession] | None,
    *,
    grid: Mapping[datetime, date] | None = None,
) -> WindowEvidence:
    step = timedelta(minutes=timeframe_minutes)
    if grid is None:
        grid = (
            scheduled_bars(sessions, timeframe_minutes)
            if sessions is not None
            else {
                bar.timestamp: bar.timestamp.astimezone(MARKET_TZ).date() for bar in prepared.bars
            }
        )
    scheduled = {ts: day for ts, day in grid.items() if start <= ts < end_exclusive}
    expected = sum(
        ts + horizon * step < end_exclusive and grid.get(ts + horizon * step) == day
        for ts, day in scheduled.items()
    )
    indices = [i for i, bar in enumerate(prepared.bars) if start <= bar.timestamp < end_exclusive]
    timestamps = [prepared.bars[i].timestamp for i in indices]
    observed_days = {ts.astimezone(MARKET_TZ).date() for ts in timestamps}
    longest = current = missing = 0
    for day in sorted(set(scheduled.values())):
        current = current + 1 if day not in observed_days else 0
        missing += day not in observed_days
        longest = max(longest, current)
    return WindowEvidence(
        start=start,
        end_exclusive=end_exclusive,
        raw_bars=len(timestamps),
        scheduled_bars=len(scheduled),
        expected_rows=expected,
        attempted_rows=len(indices),
        feature_ready_rows=sum(bool(np.isfinite(prepared.matrix[i]).all()) for i in indices),
        first_bar=timestamps[0] if timestamps else None,
        last_bar=timestamps[-1] if timestamps else None,
        missing_sessions=missing,
        longest_missing_session_run=longest,
        # Invalid warmup history is relevant too; never inspect errors beyond this split.
        input_errors=dict(Counter(code for ts, code in prepared.errors if ts < end_exclusive)),
    )


def dataset_from_prepared(
    prepared: Mapping[str, PreparedSymbol],
    symbols: Sequence[str],
    pipeline: FeaturePipeline,
    target: TargetConfig,
    start: datetime,
    end_exclusive: datetime,
    sessions: Sequence[TradingSession] | None = None,
) -> ModelDataset:
    """Entry open[t+1], exit close[t+horizon]; contiguous, same-session targets."""
    horizon = target.horizon_bars
    step = timedelta(minutes=pipeline.required_bars().timeframe.amount)
    grid = (
        scheduled_bars(sessions, pipeline.required_bars().timeframe.amount)
        if sessions is not None
        else None
    )
    records: list[tuple[datetime, str, tuple[float, ...], float | int]] = []
    evidence: dict[str, WindowEvidence] = {}
    for symbol in symbols:
        item = prepared[symbol]
        bars, matrix = item.bars, item.matrix
        stats = window_evidence(
            item,
            start,
            end_exclusive,
            int(step.total_seconds() / 60),
            horizon,
            sessions,
            grid=grid,
        )
        rejects: Counter[str] = Counter()
        # Prefix sum makes target-path continuity O(1), even for long horizons.
        gaps = np.cumsum([0, *(int(b.timestamp - a.timestamp != step) for a, b in pairwise(bars))])
        for index, bar in enumerate(bars):
            if not start <= bar.timestamp < end_exclusive:
                continue
            future = index + horizon
            target_time = bar.timestamp + horizon * step
            day = bar.timestamp.astimezone(MARKET_TZ).date()
            reason = None
            if target_time >= end_exclusive:
                reason = "split_end"
            elif (
                target_time.astimezone(MARKET_TZ).date() != day
                or not is_regular_session(target_time)
                or (grid is not None and grid.get(target_time) != day)
            ):
                reason = "session_end"
            elif future >= len(bars):
                reason = "missing_horizon"
            elif gaps[future] != gaps[index] or bars[future].timestamp != target_time:
                reason = "gapped_horizon"
            elif not np.isfinite(matrix[index]).all():
                reason = "incomplete_features"
            if reason is not None:
                rejects[reason] += 1
                continue
            entry, exit_bar = bars[index + 1], bars[future]
            # Difference of logs avoids overflowing a ratio of finite positive prices.
            forward_return = math.log(exit_bar.close) - math.log(entry.open)
            if not math.isfinite(forward_return):
                rejects["invalid_target"] += 1
                continue
            label: float | int = forward_return
            if isinstance(target, ClassificationTargetConfig):
                label = (
                    CLASS_SHORT
                    if forward_return < -target.neutral_band
                    else CLASS_LONG
                    if forward_return > target.neutral_band
                    else CLASS_FLAT
                )
            records.append((bar.timestamp, symbol, tuple(float(v) for v in matrix[index]), label))
        stats.rejections = dict(rejects)
        stats.usable_rows = stats.attempted_rows - sum(rejects.values())
        evidence[symbol] = stats
    records.sort(key=lambda record: (record[0], record[1]))
    features = np.asarray([record[2] for record in records], dtype=np.float64).reshape(
        (-1, len(pipeline.feature_names))
    )
    task = (
        ModelTask.REGRESSION
        if isinstance(target, RegressionTargetConfig)
        else ModelTask.CLASSIFICATION
    )
    labels: npt.NDArray[np.float64] | npt.NDArray[np.int64]
    if task is ModelTask.REGRESSION:
        labels = np.asarray([record[3] for record in records], dtype=np.float64)
    else:
        labels = np.asarray([record[3] for record in records], dtype=np.int64)
    return ModelDataset(
        features,
        labels,
        pipeline.feature_names,
        tuple(DatasetRow(r[1], r[0]) for r in records),
        task,
        sum(e.attempted_rows for e in evidence.values()),
        evidence,
    )


def build_dataset(
    bars_by_symbol: Mapping[str, list[Bar]],
    symbols: Sequence[str],
    pipeline: FeaturePipeline,
    target: TargetConfig,
    start: datetime,
    end_exclusive: datetime,
) -> ModelDataset:
    """Convenience wrapper; training prepares once and supplies an exchange calendar."""
    return dataset_from_prepared(
        prepare_dataset(bars_by_symbol, symbols, pipeline),
        symbols,
        pipeline,
        target,
        start,
        end_exclusive,
    )
