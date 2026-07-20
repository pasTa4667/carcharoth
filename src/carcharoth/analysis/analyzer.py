"""Reads a finished run's persisted data, computes metrics, persists them."""

import logging
from collections.abc import Mapping
from uuid import UUID

from carcharoth.analysis.metrics import compute_metrics, enrich_with_excursions, match_round_trips
from carcharoth.analysis.objective import MissingMetricError, fitness_metric_name, score_metrics
from carcharoth.config.app_config import ObjectiveConfig
from carcharoth.domain.models import MetricValue
from carcharoth.persistence.repositories import (
    AnalysisReader,
    BacktestMetricsRepository,
    RoundTripRepository,
)

logger = logging.getLogger(__name__)


class BacktestAnalyzer:
    def __init__(
        self,
        reader: AnalysisReader,
        metrics_repo: BacktestMetricsRepository,
        objectives: Mapping[str, ObjectiveConfig] | None = None,
        round_trips_repo: RoundTripRepository | None = None,
    ) -> None:
        self._reader = reader
        self._metrics_repo = metrics_repo
        self._objectives = dict(objectives) if objectives else {}
        self._round_trips_repo = round_trips_repo

    def analyze(self, run_id: UUID) -> list[MetricValue]:
        trades = self._reader.list_trades(run_id)
        equity = self._reader.list_equity(run_id)
        decisions = self._reader.list_decisions(run_id)
        assignments = self._reader.list_assignments(run_id)

        round_trips = match_round_trips(trades, decisions, assignments)
        snapshots = self._reader.list_position_snapshots(run_id)
        round_trips = enrich_with_excursions(round_trips, snapshots)
        metrics = compute_metrics(equity, trades, round_trips=round_trips)
        metrics.extend(self._fitness_metrics(metrics))
        self._metrics_repo.save_metrics(run_id, metrics)

        if self._round_trips_repo is not None:
            self._round_trips_repo.save_all(run_id, round_trips)

        logger.info(
            "analysis of run %s (%d trades, %d equity points):", run_id, len(trades), len(equity)
        )
        for metric in metrics:
            label = f"{metric.name}[{metric.symbol}]" if metric.symbol else metric.name
            logger.info("  %-24s %12.4f", label, metric.value)
        return metrics

    def _fitness_metrics(self, metrics: list[MetricValue]) -> list[MetricValue]:
        fitness: list[MetricValue] = []
        for name, objective in self._objectives.items():
            try:
                score = score_metrics(metrics, objective)
                fitness.append(MetricValue(fitness_metric_name(name), score))
            except MissingMetricError as exc:
                logger.warning("objective %r not scored: %s", name, exc)
        return fitness
