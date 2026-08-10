"""Permutation testing: bar-permutation invariants, p-value math, chunked
seed determinism, monte carlo trade-shuffle distributions, and the runner's
single end-of-run batch persist."""

import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pytest

from carcharoth.analysis.metrics import RoundTrip
from carcharoth.config.app_config import BacktestConfig, ObjectiveConfig
from carcharoth.config.quicktest_config import PermutationConfig, QuickTestConfig
from carcharoth.domain.models import Bar, MetricValue, Timeframe
from carcharoth.permutation.methods.in_sample_bars import permute_symbol_bars
from carcharoth.permutation.methods.monte_carlo_trades import MonteCarloTradesPermutation
from carcharoth.permutation.registry import (
    PERMUTATION_METHODS,
    build_permutation_method,
    method_kind,
)
from carcharoth.permutation.runner import (
    _split_indices,
    build_trade_context,
    compute_p_value,
    run_monte_carlo_test,
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


# --- monte carlo trade shuffle --------------------------------------------------


def make_round_trips(n: int, seed: int = 3) -> list[RoundTrip]:
    """Closed trades with mixed wins/losses, one per hour."""
    rng = np.random.default_rng(seed)
    trips = []
    for i in range(n):
        entry = 100.0
        exit_ = entry + float(rng.normal(1.0, 5.0))  # positive drift, real losses
        opened = WINDOW_START + timedelta(hours=i)
        trips.append(
            RoundTrip(
                symbol="AAPL",
                qty=10.0,
                entry_price=entry,
                exit_price=exit_,
                pnl=(exit_ - entry) * 10.0,
                opened_at=opened,
                closed_at=opened + timedelta(minutes=30),
            )
        )
    return trips


def _evaluate(trips: list[RoundTrip], sampled: list[RoundTrip]) -> dict[str, float]:
    ctx = build_trade_context(trips, 10_000.0)
    assert ctx.evaluate_round_trips is not None
    return ctx.evaluate_round_trips(sampled).metrics


def test_method_kinds() -> None:
    assert method_kind("in_sample_bars") == "bars"
    assert method_kind("monte_carlo_trades") == "trades"
    with pytest.raises(ValueError, match="unknown permutation method"):
        method_kind("nope")


def test_monte_carlo_rejects_bad_sampling() -> None:
    with pytest.raises(ValueError, match="unknown sampling"):
        MonteCarloTradesPermutation(sampling="bogus")


def test_shuffle_preserves_totals_but_varies_drawdown() -> None:
    trips = make_round_trips(40)
    ctx = build_trade_context(trips, 10_000.0)
    method = MonteCarloTradesPermutation(sampling="shuffle")

    baseline = _evaluate(trips, list(ctx.baseline_round_trips))
    drawdowns = set()
    for seed in range(20):
        outcome = method.permute(ctx, np.random.default_rng(seed))
        # reorder without replacement: order-independent metrics are exact
        assert outcome.metrics["total_return"] == pytest.approx(baseline["total_return"])
        assert outcome.metrics["num_trades"] == len(trips)
        assert outcome.metrics["profit_factor"] == pytest.approx(baseline["profit_factor"])
        drawdowns.add(round(outcome.metrics["max_drawdown"], 12))
    # ... while the equity path (drawdown) varies across shuffles
    assert len(drawdowns) > 1


def test_resample_varies_total_return() -> None:
    trips = make_round_trips(40)
    ctx = build_trade_context(trips, 10_000.0)
    method = MonteCarloTradesPermutation()  # default: resample

    returns = {
        round(method.permute(ctx, np.random.default_rng(seed)).metrics["total_return"], 12)
        for seed in range(20)
    }
    assert len(returns) > 1  # bootstrap changes the trade set itself


def test_trade_context_equity_grid_matches_baseline() -> None:
    trips = make_round_trips(5)
    baseline = _evaluate(trips, sorted(trips, key=lambda t: t.closed_at))
    assert baseline["final_equity"] == pytest.approx(10_000.0 + sum(t.pnl for t in trips))
    assert baseline["total_return"] == pytest.approx(sum(t.pnl for t in trips) / 10_000.0)


def test_run_monte_carlo_test_persists_null_verdict() -> None:
    trips = make_round_trips(30)
    permutation = PermutationConfig(method="monte_carlo_trades", n_permutations=25, seed=9)
    permutation_repo = RecordingPermutationRepo()
    flushes: list[dict[type[Base], list[dict[str, Any]]]] = []
    run_id = uuid4()

    outcome = run_monte_carlo_test(
        run_id=run_id,
        round_trips=trips,
        initial_capital=10_000.0,
        permutation=permutation,
        permutation_repo=permutation_repo,
        flush=lambda pending: flushes.append(dict(pending)),
    )

    # header row: linked to the baseline run, verdict columns NULL
    assert len(permutation_repo.tests) == 1
    header = permutation_repo.tests[0]
    assert header["run_id"] == run_id
    assert header["method"] == "monte_carlo_trades"
    assert header["significance"] is None
    assert header["p_value"] is None
    assert header["passed"] is None
    assert outcome.p_value is None and outcome.passed is None

    # one batched flush of the per-sample rows
    assert len(flushes) == 1
    rows = flushes[0][PermutationResultRow]
    assert len(rows) == 25
    assert {row["permutation_index"] for row in rows} == set(range(25))

    # distributions: percentile tables + observed ranks, no verdict
    for name in ("total_return", "max_drawdown", "final_equity"):
        assert set(outcome.percentiles[name]) == {"p5", "p25", "p50", "p75", "p95", "p99"}
        assert 0.0 <= outcome.observed_percentile_ranks[name] <= 100.0
    assert outcome.prob_profit is not None and 0.0 <= outcome.prob_profit <= 1.0
    assert outcome.observed_metrics["num_trades"] == len(trips)


def test_run_monte_carlo_test_is_reproducible() -> None:
    trips = make_round_trips(20)
    kwargs: dict[str, Any] = {
        "round_trips": trips,
        "initial_capital": 10_000.0,
        "permutation": PermutationConfig(method="monte_carlo_trades", n_permutations=10, seed=5),
        "flush": lambda pending: None,
    }
    once = run_monte_carlo_test(
        run_id=uuid4(), permutation_repo=RecordingPermutationRepo(), **kwargs
    )
    again = run_monte_carlo_test(
        run_id=uuid4(), permutation_repo=RecordingPermutationRepo(), **kwargs
    )
    assert once.scores == again.scores
    assert once.percentiles == again.percentiles

    other = run_monte_carlo_test(
        run_id=uuid4(),
        permutation_repo=RecordingPermutationRepo(),
        **{
            **kwargs,
            "permutation": PermutationConfig(
                method="monte_carlo_trades", n_permutations=10, seed=6
            ),
        },
    )
    assert once.scores != other.scores


def test_backtest_config_accepts_permutation_section() -> None:
    config = BacktestConfig.model_validate(
        {"permutation": {"method": "monte_carlo_trades", "n_permutations": 500, "seed": 1}}
    )
    assert config.permutation is not None
    assert config.permutation.method == "monte_carlo_trades"
    assert config.permutation.n_permutations == 500
    assert BacktestConfig().permutation is None


def test_run_monte_carlo_test_rejects_bar_methods_and_empty_trades() -> None:
    with pytest.raises(ValueError, match="not a trade-based method"):
        run_monte_carlo_test(
            run_id=uuid4(),
            round_trips=make_round_trips(3),
            initial_capital=10_000.0,
            permutation=PermutationConfig(method="in_sample_bars", n_permutations=1),
            permutation_repo=RecordingPermutationRepo(),
            flush=lambda pending: None,
        )
    with pytest.raises(ValueError, match="at least one closed round trip"):
        run_monte_carlo_test(
            run_id=uuid4(),
            round_trips=[],
            initial_capital=10_000.0,
            permutation=PermutationConfig(method="monte_carlo_trades", n_permutations=1),
            permutation_repo=RecordingPermutationRepo(),
            flush=lambda pending: None,
        )
