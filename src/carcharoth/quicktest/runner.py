"""Fixed-artifact quicktest orchestration and batched persistence."""

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
from carcharoth.analysis.objective import MissingMetricError, fitness_metric_name, score_metrics
from carcharoth.config.app_config import ObjectiveConfig
from carcharoth.config.quicktest_config import QuickTestConfig
from carcharoth.domain.models import Bar, MetricValue, RunType
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.interfaces.optimization import BarsFetcher
from carcharoth.modeling.signal_generator import build_signal_generator
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

logger = logging.getLogger(__name__)
BarsTransform = Callable[[dict[str, list[Bar]]], dict[str, list[Bar]]]


@dataclass(frozen=True, slots=True)
class QuickTestOutcome:
    run_id: UUID
    result: QuickTestResult
    round_trips: list[RoundTrip]
    metrics: list[MetricValue]


def fetch_quicktest_bars(config: QuickTestConfig, fetch_bars: BarsFetcher) -> dict[str, list[Bar]]:
    pipeline = FeaturePipeline(
        config.model.features,
        config.model.timeframe_minutes,
        config.model.benchmark_symbol,
    )
    spec = pipeline.required_bars()
    symbols = list(dict.fromkeys([*config.symbols, config.model.benchmark_symbol]))
    return fetch_bars(
        symbols,
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
    model = build_signal_generator(config.model, evaluation_start=config.start)
    if config.model.prepared_history:
        model.prepare_history(
            bars,
            symbols=config.symbols,
            start=config.start_dt,
            end_exclusive=config.end_exclusive_dt,
        )
    settings = simulation_settings(config)
    result = QuickTestResult(capital_per_symbol=config.capital)
    benchmark = bars.get(config.model.benchmark_symbol, [])
    for symbol in config.symbols:
        result.symbols[symbol] = simulate_symbol(
            model,
            symbol,
            bars.get(symbol, []),
            benchmark,
            config.start_dt,
            config.end_exclusive_dt,
            settings,
        )
    model_id = config.model.artifact_path.name if config.model.artifact_path else ""
    round_trips = analyze_round_trips(result, model_id)
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
    started_at = datetime.now(UTC)
    if bars is None:
        bars = fetch_quicktest_bars(config, fetch_bars)
    if bars_transform is not None:
        bars = bars_transform(bars)
    logger.info(
        "quicktest: model=%s, %d symbols, %d bars, %s to %s",
        config.model.artifact_path,
        len(config.symbols),
        sum(len(values) for values in bars.values()),
        config.start,
        config.end,
    )
    result, round_trips, metrics = simulate_and_analyze(config, objectives, bars)
    run_id = persist_quicktest(
        config,
        result,
        round_trips,
        metrics,
        started_at,
        runs_repo,
        flush,
        round_trips_repo,
        metrics_repo,
    )
    return QuickTestOutcome(run_id, result, round_trips, metrics)


def analyze_round_trips(result: QuickTestResult, model_id: str) -> list[RoundTrip]:
    trips = enrich_with_excursions(match_round_trips(result.trades), result.snapshots)
    return [replace(trip, model_id=model_id) for trip in trips]


def compute_quicktest_metrics(
    result: QuickTestResult,
    round_trips: list[RoundTrip],
    objectives: Mapping[str, ObjectiveConfig],
) -> list[MetricValue]:
    metrics = compute_metrics(result.aggregate_equity(), result.trades, round_trips=round_trips)
    for name, objective in objectives.items():
        try:
            metrics.append(
                MetricValue(fitness_metric_name(name), score_metrics(metrics, objective))
            )
        except MissingMetricError as exc:
            logger.warning("objective %r not scored: %s", name, exc)
    trips_by_symbol: dict[str, list[RoundTrip]] = {}
    for trip in round_trips:
        trips_by_symbol.setdefault(trip.symbol, []).append(trip)
    for symbol, symbol_result in result.symbols.items():
        symbol_metrics = compute_metrics(
            symbol_result.equity,
            symbol_result.trades,
            round_trips=trips_by_symbol.get(symbol, []),
        )
        metrics.extend(
            MetricValue(metric.name, metric.value, symbol=symbol)
            for metric in symbol_metrics
            if metric.symbol is None
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
                "buying_power": Decimal(str(cash.equity)),
            },
        )
    buffer.flush()
    round_trips_repo.save_all(run_id, round_trips)
    metrics_repo.save_metrics(run_id, metrics)
    runs_repo.finish(run_id, datetime.now(UTC))
    return run_id
