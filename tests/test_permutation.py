"""Permutation testing: bar-permutation invariants, p-value math, chunked
seed determinism, and the runner's single end-of-run batch persist."""

import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pytest

from carcharoth.analysis.metrics import RoundTrip
from carcharoth.config.app_config import ObjectiveConfig
from carcharoth.config.quicktest_config import PermutationConfig, QuickTestConfig
from carcharoth.domain.models import Bar, MetricValue, Timeframe
from carcharoth.permutation.methods.in_sample_bars import permute_symbol_bars
from carcharoth.permutation.registry import PERMUTATION_METHODS, build_permutation_method
from carcharoth.permutation.runner import (
    _split_indices,
    compute_p_value,
    run_permutation_chunk,
    run_permutation_test,
)
from carcharoth.persistence.orm import Base, PermutationResultRow
from carcharoth.persistence.repositories import (
    BacktestMetricsRepository,
    PermutationRepository,
    RoundTripRepository,
)
from tests.fakes import InMemoryRunRepository

# 14:00 UTC on 2026-07-01 is 10:00 New York: inside the regular session.
SESSION_START = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 7, 1, tzinfo=UTC)

OBJECTIVES = {"default": ObjectiveConfig(weights={"total_return": 1.0})}


def make_ohlc_bars(n: int, start: datetime, seed: int = 7, symbol: str = "AAPL") -> list[Bar]:
    """Random-walk bars with genuine intrabar structure (high > open/close > low)."""
    rng = np.random.default_rng(seed)
    close = 100.0
    bars = []
    for i in range(n):
        open_ = close * math.exp(rng.normal(0, 0.001))
        close = open_ * math.exp(rng.normal(0, 0.002))
        high = max(open_, close) * math.exp(abs(rng.normal(0, 0.001)))
        low = min(open_, close) * math.exp(-abs(rng.normal(0, 0.001)))
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=start + timedelta(minutes=5 * i),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=float(rng.integers(100, 10_000)),
            )
        )
    return bars


# --- in_sample_bars -----------------------------------------------------------


def test_permute_preserves_structure() -> None:
    warmup = make_ohlc_bars(10, SESSION_START - timedelta(hours=1))
    window = make_ohlc_bars(50, SESSION_START, seed=8)
    bars = warmup + window
    start = SESSION_START

    permuted = permute_symbol_bars(bars, start, np.random.default_rng(1))

    assert len(permuted) == len(bars)
    assert [b.timestamp for b in permuted] == [b.timestamp for b in bars]
    assert all(b.symbol == "AAPL" for b in permuted)
    # warm-up bars are untouched
    assert permuted[:10] == warmup
    # the window actually changed
    assert permuted[10:] != window
    # per-bar OHLC invariants survive (intrabar tuples move as one unit)
    for bar in permuted[10:]:
        assert bar.high >= max(bar.open, bar.close)
        assert bar.low <= min(bar.open, bar.close)
        assert bar.low > 0
    # volumes are a permutation of the originals
    assert Counter(b.volume for b in permuted[10:]) == Counter(b.volume for b in window)
    # gaps and intrabar returns are shuffled, not changed: the walk ends where
    # the real one did (sum of log returns is permutation-invariant)
    assert permuted[-1].close == pytest.approx(window[-1].close)


def test_permute_is_deterministic_per_seed() -> None:
    bars = make_ohlc_bars(30, SESSION_START)
    once = permute_symbol_bars(bars, SESSION_START, np.random.default_rng(42))
    again = permute_symbol_bars(bars, SESSION_START, np.random.default_rng(42))
    other = permute_symbol_bars(bars, SESSION_START, np.random.default_rng(43))
    assert once == again
    assert once != other


def test_permute_short_window_is_identity() -> None:
    bars = make_ohlc_bars(1, SESSION_START)
    assert permute_symbol_bars(bars, SESSION_START, np.random.default_rng(0)) == bars


def test_registry_builds_and_rejects() -> None:
    assert "in_sample_bars" in PERMUTATION_METHODS
    assert build_permutation_method("in_sample_bars", {}).name == "in_sample_bars"
    with pytest.raises(ValueError, match="unknown permutation method"):
        build_permutation_method("nope", {})


# --- p-value ------------------------------------------------------------------


def test_p_value_counts_ties_and_better() -> None:
    # 2 of 4 permutations >= observed (one tie, one better): (1+2)/(4+1)
    assert compute_p_value(1.0, [0.5, 1.0, 1.5, 0.0]) == pytest.approx(3 / 5)


def test_p_value_never_zero() -> None:
    assert compute_p_value(10.0, [0.0] * 99) == pytest.approx(1 / 100)


def test_p_value_no_permutations() -> None:
    assert compute_p_value(1.0, []) == 1.0


# --- chunked seeding ----------------------------------------------------------


def test_split_indices_covers_all() -> None:
    chunks = _split_indices(10, 4)
    assert [len(c) for c in chunks] == [3, 3, 2, 2]
    assert sorted(i for c in chunks for i in c) == list(range(10))
    assert _split_indices(2, 8) == [[0], [1]]


def quicktest_config(**overrides: Any) -> QuickTestConfig:
    payload: dict[str, Any] = {
        "symbols": ["AAPL", "MSFT"],
        "start": WINDOW_START.date(),
        "end": WINDOW_START.date(),
        "strategy": {"name": "mean_reversion", "params": {}},
        **overrides,
    }
    return QuickTestConfig.model_validate(payload)


def test_chunking_does_not_change_scores() -> None:
    """Permutation i is seeded by (seed, i), so any chunking yields the same
    scores — the guarantee that makes worker count irrelevant to results."""
    config = quicktest_config()
    bars = {
        "AAPL": make_ohlc_bars(60, SESSION_START, seed=1, symbol="AAPL"),
        "MSFT": make_ohlc_bars(60, SESSION_START, seed=2, symbol="MSFT"),
    }
    args = (config, OBJECTIVES["default"], bars, "in_sample_bars", {}, 42)

    whole = run_permutation_chunk(*args, list(range(6)))
    chunked = [
        record for chunk in _split_indices(6, 3) for record in run_permutation_chunk(*args, chunk)
    ]
    assert sorted(chunked) == sorted(whole)
    assert len({index for index, _, _ in whole}) == 6


# --- runner persistence -------------------------------------------------------


class RecordingRoundTripRepo(RoundTripRepository):
    def __init__(self) -> None:
        self.saved: dict[UUID, list[RoundTrip]] = {}

    def save_all(self, run_id, round_trips) -> None:  # type: ignore[no-untyped-def]
        self.saved[run_id] = list(round_trips)


class RecordingMetricsRepo(BacktestMetricsRepository):
    def __init__(self) -> None:
        self.saved: dict[UUID, list[MetricValue]] = {}

    def save_metrics(self, run_id, metrics) -> None:  # type: ignore[no-untyped-def]
        self.saved[run_id] = list(metrics)


class RecordingPermutationRepo(PermutationRepository):
    def __init__(self) -> None:
        self.tests: list[dict[str, Any]] = []

    def create_test(self, **kwargs: Any) -> UUID:
        test_id = uuid4()
        self.tests.append({"test_id": test_id, **kwargs})
        return test_id


def test_run_permutation_test_persists_one_batch() -> None:
    config = quicktest_config()
    bars = {
        "AAPL": make_ohlc_bars(60, SESSION_START, seed=1, symbol="AAPL"),
        "MSFT": make_ohlc_bars(60, SESSION_START, seed=2, symbol="MSFT"),
    }

    def fetch_bars(
        symbols: list[str], timeframe: Timeframe, start: datetime, end: datetime
    ) -> dict[str, list[Bar]]:
        return bars

    permutation = PermutationConfig(n_permutations=5, seed=7, workers=1)
    runs_repo = InMemoryRunRepository()
    permutation_repo = RecordingPermutationRepo()
    flushes: list[dict[type[Base], list[dict[str, Any]]]] = []

    baseline, outcome = run_permutation_test(
        config,
        permutation,
        OBJECTIVES,
        fetch_bars,
        runs_repo=runs_repo,
        flush=lambda pending: flushes.append(dict(pending)),
        round_trips_repo=RecordingRoundTripRepo(),
        metrics_repo=RecordingMetricsRepo(),
        permutation_repo=permutation_repo,
        workers=1,
    )

    # exactly one test header row, linked to the baseline quicktest run
    assert len(permutation_repo.tests) == 1
    header = permutation_repo.tests[0]
    assert header["run_id"] == baseline.run_id
    assert header["method"] == "in_sample_bars"
    assert header["n_permutations"] == 5
    assert header["p_value"] == outcome.p_value

    # per-permutation rows arrive in one flush, after the baseline's own batch
    permutation_flushes = [f for f in flushes if PermutationResultRow in f]
    assert len(permutation_flushes) == 1
    rows = permutation_flushes[0][PermutationResultRow]
    assert len(rows) == 5
    assert {row["permutation_index"] for row in rows} == set(range(5))
    assert all(row["test_id"] == outcome.test_id for row in rows)

    assert len(outcome.scores) == 5
    assert 0.0 < outcome.p_value <= 1.0
    assert outcome.passed == (outcome.p_value <= permutation.significance)


def test_run_permutation_test_requires_known_objective() -> None:
    config = quicktest_config(objective="missing")
    with pytest.raises(ValueError, match="objective 'missing'"):
        run_permutation_test(
            config,
            PermutationConfig(n_permutations=1, workers=1),
            OBJECTIVES,
            lambda *a, **k: {},
            runs_repo=InMemoryRunRepository(),
            flush=lambda pending: None,
            round_trips_repo=RecordingRoundTripRepo(),
            metrics_repo=RecordingMetricsRepo(),
            permutation_repo=RecordingPermutationRepo(),
        )


def test_permutation_config_rejects_unknown_method() -> None:
    with pytest.raises(ValueError, match="unknown permutation method"):
        PermutationConfig(method="nope")
