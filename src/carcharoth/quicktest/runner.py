"""Quick-test orchestration: fetch bars -> simulate -> analyze -> persist once.

Everything up to persistence is pure/in-memory; the single batch write at the
end reuses the existing run/trade/round-trip/metric tables so quick tests
show up next to backtests (run_type QUICKTEST) in Grafana.

The ``BarsTransform`` seam exists for future permutation testing: a transform
is applied to the fetched bars before simulation (identity when None).
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from carcharoth.analysis.metrics import (
    RoundTrip,
    compute_metrics,
    enrich_with_excursions,
    match_round_trips,
)
from carcharoth.analysis.objective import (
    MissingMetricError,
    fitness_metric_name,
    score_metrics,
)
from carcharoth.config.app_config import ObjectiveConfig
from carcharoth.config.quicktest_config import QuickTestConfig
from carcharoth.domain.models import Bar, MetricValue, RunType
from carcharoth.interfaces.optimization import BarsFetcher
from carcharoth.persistence.buffered import FlushFn, WriteBuffer
from carcharoth.persistence.orm import EquitySnapshotRow, TradeRow
from carcharoth.persistence.repositories import (
    BacktestMetricsRepository,
    RoundTripRepository,
    RunRepository,
)
from carcharoth.quicktest.result import QuickTestResult
from carcharoth.quicktest.simulator import SimulationSettings, simulate_symbol
from carcharoth.services.alpaca.historical import warmup_window
from carcharoth.strategies.registry import build_strategy

logger = logging.getLogger(__name__)

#: Seam for future permutation testing: bars in, (permuted) bars out.
BarsTransform = Callable[[dict[str, list[Bar]]], dict[str, list[Bar]]]


@dataclass(frozen=True, slots=True)
class QuickTestOutcome:
    """The persisted run id plus everything computed in memory."""

    run_id: UUID
    result: QuickTestResult
    round_trips: list[RoundTrip]
    #: aggregate (symbol=None) and per-symbol (symbol set) metrics + fitness
    metrics: list[MetricValue]


def fetch_quicktest_bars(
    config: QuickTestConfig, fetch_bars: BarsFetcher
) -> dict[str, list[Bar]]:
    """Fetch the run's bars once (warm-up included) so callers — e.g. the
    permutation runner — can reuse them across many simulations."""
    strategy = build_strategy(config.strategy.name, config.strategy.params)
    spec = strategy.required_bars()
    return fetch_bars(
        config.symbols,
        spec.timeframe,
        config.start_dt - warmup_window(spec),
        config.end_exclusive_dt,
    )


def simulation_settings(config: QuickTestConfig) -> SimulationSettings:
    return SimulationSettings(
        capital=config.capital,
        position_size_pct=config.position_size_pct,
        spread_pct=config.spread_pct,
        slippage_pct=config.slippage_pct,
    )


def simulate_and_analyze(
    config: QuickTestConfig,
    objectives: Mapping[str, ObjectiveConfig],
    bars: Mapping[str, list[Bar]],
) -> tuple[QuickTestResult, list[RoundTrip], list[MetricValue]]:
    """The pure in-memory core of one quick test over pre-fetched bars:
    simulate every symbol independently (fresh strategy — strategies are
    stateful), then compute round trips and metrics. No I/O."""
    strategy = build_strategy(config.strategy.name, config.strategy.params)
    start, end_exclusive = config.start_dt, config.end_exclusive_dt
    settings = simulation_settings(config)
    result = QuickTestResult(capital_per_symbol=config.capital)
    for symbol in config.symbols:
        result.symbols[symbol] = simulate_symbol(
            strategy, symbol, bars.get(symbol, []), start, end_exclusive, settings
        )
    round_trips = analyze_round_trips(result, config.strategy.name)
    metrics = compute_quicktest_metrics(result, round_trips, objectives)
    return result, round_trips, metrics


def run_quicktest_once(
    config: QuickTestConfig,
    objectives: Mapping[str, ObjectiveConfig],
    fetch_bars: BarsFetcher,
    runs_repo: RunRepository,
    flush: FlushFn,
    round_trips_repo: RoundTripRepository,
    metrics_repo: BacktestMetricsRepository,
    bars_transform: BarsTransform | None = None,
    bars: dict[str, list[Bar]] | None = None,
) -> QuickTestOutcome:
    """One quick test: fetch (unless ``bars`` is given), simulate all symbols
    independently, compute metrics in memory, then persist everything in a
    single end-of-run batch."""
    started_at = datetime.now(UTC)
    if bars is None:
        bars = fetch_quicktest_bars(config, fetch_bars)
    if bars_transform is not None:
        bars = bars_transform(bars)
    total_bars = sum(len(symbol_bars) for symbol_bars in bars.values())
    logger.info(
        "quicktest: strategy=%s, %d symbols, %d bars, %s to %s",
        config.strategy.name,
        len(config.symbols),
        total_bars,
        config.start,
        config.end,
    )

    result, round_trips, metrics = simulate_and_analyze(config, objectives, bars)

    run_id = persist_quicktest(
        config=config,
        result=result,
        round_trips=round_trips,
        metrics=metrics,
        started_at=started_at,
        runs_repo=runs_repo,
        flush=flush,
        round_trips_repo=round_trips_repo,
        metrics_repo=metrics_repo,
    )
    logger.info("quicktest run %s complete (%d round trips)", run_id, len(round_trips))
    return QuickTestOutcome(run_id=run_id, result=result, round_trips=round_trips, metrics=metrics)


def analyze_round_trips(result: QuickTestResult, strategy_name: str) -> list[RoundTrip]:
    """FIFO-match all fills into round trips, enriched with MAE/MFE and
    tagged with the strategy (there are no decision rows to look it up from)."""
    round_trips = match_round_trips(result.trades)
    round_trips = enrich_with_excursions(round_trips, result.snapshots)
    return [replace(trip, strategy=strategy_name) for trip in round_trips]


def compute_quicktest_metrics(
    result: QuickTestResult,
    round_trips: list[RoundTrip],
    objectives: Mapping[str, ObjectiveConfig],
) -> list[MetricValue]:
    """Aggregate metrics (symbol=None) + fitness per named objective, plus the
    same portfolio-level metrics per symbol (symbol set) for edge comparison."""
    metrics = compute_metrics(result.aggregate_equity(), result.trades, round_trips=round_trips)
    for name, objective in objectives.items():
        try:
            score = score_metrics(metrics, objective)
            metrics.append(MetricValue(fitness_metric_name(name), score))
        except MissingMetricError as exc:
            logger.warning("objective %r not scored: %s", name, exc)

    trips_by_symbol: dict[str, list[RoundTrip]] = {}
    for trip in round_trips:
        trips_by_symbol.setdefault(trip.symbol, []).append(trip)
    for symbol, sym_result in result.symbols.items():
        sym_metrics = compute_metrics(
            sym_result.equity, sym_result.trades, round_trips=trips_by_symbol.get(symbol, [])
        )
        metrics.extend(
            MetricValue(m.name, m.value, symbol=symbol) for m in sym_metrics if m.symbol is None
        )
    return metrics


def persist_quicktest(
    config: QuickTestConfig,
    result: QuickTestResult,
    round_trips: list[RoundTrip],
    metrics: list[MetricValue],
    started_at: datetime,
    runs_repo: RunRepository,
    flush: FlushFn,
    round_trips_repo: RoundTripRepository,
    metrics_repo: BacktestMetricsRepository,
) -> UUID:
    """The single end-of-run batch write: run row, all trades, the aggregate
    equity curve, round trips, and metrics."""
    run_id = runs_repo.create(
        run_type=RunType.QUICKTEST,
        config=config.model_dump(mode="json"),
        symbols=config.symbols,
        started_at=started_at,
        backtest_start=config.start_dt,
        backtest_end=config.end_exclusive_dt,
    )
    buffer = WriteBuffer(flush)
    for trade in result.trades:
        buffer.add(
            TradeRow,
            {
                "run_id": run_id,
                "broker_order_id": uuid4().hex,
                "symbol": trade.symbol,
                "side": trade.side.value,
                "qty": Decimal(str(trade.qty)),
                "price": Decimal(str(trade.price)),
                "timestamp": trade.timestamp,
            },
        )
    for equity, cash in zip(result.aggregate_equity(), result.aggregate_cash(), strict=True):
        buffer.add(
            EquitySnapshotRow,
            {
                "run_id": run_id,
                "timestamp": equity.timestamp,
                "equity": Decimal(str(equity.equity)),
                "cash": Decimal(str(cash.equity)),
                "buying_power": Decimal(str(cash.equity)),  # cash account, no margin
            },
        )
    buffer.flush()
    round_trips_repo.save_all(run_id, round_trips)
    metrics_repo.save_metrics(run_id, metrics)
    runs_repo.finish(run_id, datetime.now(UTC))
    return run_id
