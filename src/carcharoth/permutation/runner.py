"""Permutation-test orchestration: baseline quicktest → N permuted re-runs in
parallel → p-value → one batched persist.

Everything inside the permutation loop is pure and in-memory. Permutation ``i``
always uses ``SeedSequence([seed, i])``, so results are reproducible for a
given master seed regardless of worker count or completion order. Workers
return only small ``(index, score, headline_metrics)`` tuples — no curves, no
trades — and nothing is written to the database until every permutation has
finished.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from carcharoth.analysis.metrics import compute_metrics, match_round_trips
from carcharoth.analysis.objective import (
    MissingMetricError,
    fitness_metric_name,
    score_metrics,
)
from carcharoth.interfaces.permutation import PermutationContext, PermutedOutcome
from carcharoth.permutation.registry import build_permutation_method
from carcharoth.persistence.buffered import WriteBuffer
from carcharoth.persistence.orm import PermutationResultRow
from carcharoth.quicktest.result import QuickTestResult
from carcharoth.quicktest.runner import (
    fetch_quicktest_bars,
    run_quicktest_once,
    simulation_settings,
)
from carcharoth.quicktest.simulator import simulate_symbol
from carcharoth.strategies.registry import build_strategy

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from carcharoth.config.app_config import ObjectiveConfig
    from carcharoth.config.quicktest_config import PermutationConfig, QuickTestConfig
    from carcharoth.domain.models import Bar, MetricValue
    from carcharoth.interfaces.optimization import BarsFetcher
    from carcharoth.persistence.buffered import FlushFn
    from carcharoth.persistence.repositories import (
        BacktestMetricsRepository,
        PermutationRepository,
        RoundTripRepository,
        RunRepository,
    )
    from carcharoth.quicktest.runner import QuickTestOutcome

logger = logging.getLogger(__name__)

#: portfolio-level metric names copied into each permutation's headline dict
_HEADLINE_METRICS = ("total_return", "max_drawdown", "sharpe", "profit_factor", "num_trades")

#: one (index, score, headline metrics) triple per permutation
PermutationRecord = tuple[int, float, dict[str, float]]


@dataclass(frozen=True, slots=True)
class PermutationTestOutcome:
    """The persisted test id plus everything needed for the summary."""

    test_id: UUID
    run_id: UUID
    method: str
    n_permutations: int
    seed: int
    objective: str
    significance: float
    observed_score: float
    p_value: float
    passed: bool
    #: permuted scores ordered by permutation index
    scores: list[float]


def compute_p_value(observed_score: float, permuted_scores: Sequence[float]) -> float:
    """Right-tailed permutation p-value with the +1 correction (the observed
    run counts as one permutation), so p is never exactly 0."""
    at_least_as_good = sum(1 for score in permuted_scores if score >= observed_score)
    return (1 + at_least_as_good) / (len(permuted_scores) + 1)


def build_permutation_context(
    config: QuickTestConfig,
    objective: ObjectiveConfig,
    bars: Mapping[str, list[Bar]],
) -> PermutationContext:
    """Context over pre-fetched bars whose ``simulate`` runs the lean quicktest
    core (no MAE/MFE enrichment, no per-symbol metrics) and scores it."""

    def simulate(permuted_bars: Mapping[str, list[Bar]]) -> PermutedOutcome:
        strategy = build_strategy(config.strategy.name, config.strategy.params)
        settings = simulation_settings(config)
        result = QuickTestResult(capital_per_symbol=config.capital)
        for symbol in config.symbols:
            result.symbols[symbol] = simulate_symbol(
                strategy,
                symbol,
                permuted_bars.get(symbol, []),
                config.start_dt,
                config.end_exclusive_dt,
                settings,
            )
        equity = result.aggregate_equity()
        round_trips = match_round_trips(result.trades)
        metrics = compute_metrics(equity, result.trades, round_trips=round_trips)
        try:
            score = score_metrics(metrics, objective)
        except MissingMetricError:
            score = objective.penalty_score
        headline = {m.name: m.value for m in metrics if m.symbol is None}
        headline = {name: headline[name] for name in _HEADLINE_METRICS if name in headline}
        if equity:
            headline["final_equity"] = equity[-1].equity
        return PermutedOutcome(score=score, metrics=headline)

    return PermutationContext(
        bars=bars,
        start=config.start_dt,
        end_exclusive=config.end_exclusive_dt,
        simulate=simulate,
    )


def run_permutation_chunk(
    config: QuickTestConfig,
    objective: ObjectiveConfig,
    bars: dict[str, list[Bar]],
    method_name: str,
    method_params: dict[str, Any],
    seed: int,
    indices: list[int],
) -> list[PermutationRecord]:
    """One worker's share of permutations (also the sequential path). Must be
    a module-level function: it is pickled to spawn-context workers."""
    method = build_permutation_method(method_name, method_params)
    ctx = build_permutation_context(config, objective, bars)
    records: list[PermutationRecord] = []
    for index in indices:
        rng = np.random.default_rng(np.random.SeedSequence([seed, index]))
        outcome = method.permute(ctx, rng)
        records.append((index, outcome.score, outcome.metrics))
    return records


def _split_indices(total: int, chunks: int) -> list[list[int]]:
    """Contiguous, near-equal chunks; earlier chunks take the remainder."""
    base, extra = divmod(total, chunks)
    result: list[list[int]] = []
    cursor = 0
    for chunk in range(chunks):
        size = base + (1 if chunk < extra else 0)
        if size:
            result.append(list(range(cursor, cursor + size)))
        cursor += size
    return result


def run_permutations(
    config: QuickTestConfig,
    objective: ObjectiveConfig,
    bars: dict[str, list[Bar]],
    permutation: PermutationConfig,
    workers: int,
) -> list[PermutationRecord]:
    """All N permutations, in-process when workers <= 1, otherwise fanned out
    to a spawn-context process pool (one chunk per worker so the bars dict is
    pickled at most ``workers`` times). Results are returned ordered by index."""
    n = permutation.n_permutations
    args = (config, objective, bars, permutation.method, permutation.params, permutation.seed)
    if workers <= 1 or n == 1:
        records = run_permutation_chunk(*args, list(range(n)))
    else:
        chunks = _split_indices(n, min(workers, n))
        records = []
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=len(chunks), mp_context=context) as pool:
            futures = [pool.submit(run_permutation_chunk, *args, chunk) for chunk in chunks]
            for done, future in enumerate(futures, start=1):
                records.extend(future.result())
                logger.info("permutations: chunk %d/%d complete", done, len(chunks))
    return sorted(records, key=lambda record: record[0])


def resolve_workers(configured: int) -> int:
    """0 = auto (cpu count); otherwise the configured value, capped at cpu count."""
    cpus = os.cpu_count() or 1
    return cpus if configured <= 0 else min(configured, cpus)


def observed_fitness(metrics: Sequence[MetricValue], objective_name: str) -> float:
    """The baseline run's fitness for the named objective (already computed
    and persisted by the quicktest)."""
    name = fitness_metric_name(objective_name)
    for metric in metrics:
        if metric.name == name and metric.symbol is None:
            return metric.value
    raise ValueError(
        f"baseline run has no {name!r} metric; is objective {objective_name!r} scoreable?"
    )


def run_permutation_test(
    config: QuickTestConfig,
    permutation: PermutationConfig,
    objectives: Mapping[str, ObjectiveConfig],
    fetch_bars: BarsFetcher,
    runs_repo: RunRepository,
    flush: FlushFn,
    round_trips_repo: RoundTripRepository,
    metrics_repo: BacktestMetricsRepository,
    permutation_repo: PermutationRepository,
    workers: int | None = None,
) -> tuple[QuickTestOutcome, PermutationTestOutcome]:
    """Baseline quicktest (persisted as a normal QUICKTEST run) + N permuted
    re-runs, then one batched write of the verdict and per-permutation rows."""
    if config.objective not in objectives:
        raise ValueError(
            f"objective {config.objective!r} not defined; available: {sorted(objectives)}"
        )
    objective = objectives[config.objective]
    effective_workers = resolve_workers(permutation.workers if workers is None else workers)

    bars = fetch_quicktest_bars(config, fetch_bars)
    baseline = run_quicktest_once(
        config,
        objectives,
        fetch_bars,
        runs_repo=runs_repo,
        flush=flush,
        round_trips_repo=round_trips_repo,
        metrics_repo=metrics_repo,
        bars=bars,
    )
    observed_score = observed_fitness(baseline.metrics, config.objective)

    logger.info(
        "permutation test: method=%s n=%d seed=%d workers=%d observed_score=%.6f",
        permutation.method,
        permutation.n_permutations,
        permutation.seed,
        effective_workers,
        observed_score,
    )
    records = run_permutations(config, objective, bars, permutation, effective_workers)

    scores = [score for _, score, _ in records]
    p_value = compute_p_value(observed_score, scores)
    passed = p_value <= permutation.significance

    test_id = permutation_repo.create_test(
        run_id=baseline.run_id,
        method=permutation.method,
        params=permutation.params,
        n_permutations=permutation.n_permutations,
        seed=permutation.seed,
        objective=config.objective,
        significance=permutation.significance,
        observed_score=observed_score,
        p_value=p_value,
        passed=passed,
        created_at=datetime.now(UTC),
    )
    buffer = WriteBuffer(flush)
    for index, score, headline in records:
        buffer.add(
            PermutationResultRow,
            {
                "test_id": test_id,
                "permutation_index": index,
                "score": score,
                "total_return": headline.get("total_return"),
                "max_drawdown": headline.get("max_drawdown"),
                "sharpe": headline.get("sharpe"),
                "profit_factor": headline.get("profit_factor"),
                "num_round_trips": int(headline.get("num_trades", 0)),
                "final_equity": headline.get("final_equity"),
            },
        )
    buffer.flush()

    outcome = PermutationTestOutcome(
        test_id=test_id,
        run_id=baseline.run_id,
        method=permutation.method,
        n_permutations=permutation.n_permutations,
        seed=permutation.seed,
        objective=config.objective,
        significance=permutation.significance,
        observed_score=observed_score,
        p_value=p_value,
        passed=passed,
        scores=scores,
    )
    logger.info(
        "permutation test %s complete: p_value=%.4f (%s)",
        test_id,
        p_value,
        "PASS" if passed else "FAIL",
    )
    return baseline, outcome
