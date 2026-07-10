"""Reads a finished run's persisted data, computes metrics, persists them."""

import logging
from uuid import UUID

from carcharoth.analysis.metrics import compute_metrics
from carcharoth.domain.models import MetricValue
from carcharoth.persistence.repositories import AnalysisReader, BacktestMetricsRepository

logger = logging.getLogger(__name__)


class BacktestAnalyzer:
    def __init__(self, reader: AnalysisReader, metrics_repo: BacktestMetricsRepository) -> None:
        self._reader = reader
        self._metrics_repo = metrics_repo

    def analyze(self, run_id: UUID) -> list[MetricValue]:
        trades = self._reader.list_trades(run_id)
        equity = self._reader.list_equity(run_id)
        metrics = compute_metrics(equity, trades)
        self._metrics_repo.save_metrics(run_id, metrics)

        logger.info(
            "analysis of run %s (%d trades, %d equity points):", run_id, len(trades), len(equity)
        )
        for metric in metrics:
            label = f"{metric.name}[{metric.symbol}]" if metric.symbol else metric.name
            logger.info("  %-24s %12.4f", label, metric.value)
        return metrics
