"""Fitness scoring (weighted composite over metrics) and analyzer wiring."""

from datetime import timedelta
from uuid import uuid4

import pytest

from carcharoth.analysis.analyzer import BacktestAnalyzer
from carcharoth.analysis.objective import (
    MissingMetricError,
    fitness_metric_name,
    score_metrics,
)
from carcharoth.config.app_config import ObjectiveConfig
from carcharoth.domain.models import EquityPoint, MetricValue
from tests.factories import BASE_TIME
from tests.fakes import InMemoryAnalysisReader, InMemoryBacktestMetricsRepository


def metric(name: str, value: float, symbol: str | None = None) -> MetricValue:
    return MetricValue(name=name, value=value, symbol=symbol)


def test_weighted_sum() -> None:
    objective = ObjectiveConfig(weights={"sharpe": 1.0, "total_return": 0.5})
    score = score_metrics([metric("sharpe", 2.0), metric("total_return", 0.1)], objective)
    assert score == pytest.approx(2.0 + 0.05)


def test_negative_weight_penalizes_metric() -> None:
    objective = ObjectiveConfig(weights={"sharpe": 1.0, "max_drawdown": -2.0})
    score = score_metrics([metric("sharpe", 1.0), metric("max_drawdown", 0.25)], objective)
    assert score == pytest.approx(1.0 - 0.5)


def test_symbol_metrics_are_ignored() -> None:
    objective = ObjectiveConfig(weights={"symbol_pnl": 1.0}, on_missing_metric="zero")
    assert score_metrics([metric("symbol_pnl", 500.0, symbol="AAPL")], objective) == 0.0


def test_missing_metric_penalize() -> None:
    objective = ObjectiveConfig(weights={"sharpe": 1.0, "profit_factor": 1.0}, penalty_score=-99.0)
    assert score_metrics([metric("sharpe", 3.0)], objective) == -99.0


def test_missing_metric_zero() -> None:
    objective = ObjectiveConfig(
        weights={"sharpe": 1.0, "profit_factor": 1.0}, on_missing_metric="zero"
    )
    assert score_metrics([metric("sharpe", 3.0)], objective) == pytest.approx(3.0)


def test_missing_metric_fail() -> None:
    objective = ObjectiveConfig(weights={"profit_factor": 1.0}, on_missing_metric="fail")
    with pytest.raises(MissingMetricError):
        score_metrics([metric("sharpe", 3.0)], objective)


def test_empty_weights_rejected() -> None:
    with pytest.raises(ValueError):
        ObjectiveConfig(weights={})


def test_analyzer_emits_fitness_metric_per_objective() -> None:
    run_id = uuid4()
    equity = [
        EquityPoint(timestamp=BASE_TIME + timedelta(minutes=5 * i), equity=value)
        for i, value in enumerate([100_000.0, 101_000.0, 102_010.0])
    ]
    metrics_repo = InMemoryBacktestMetricsRepository()
    analyzer = BacktestAnalyzer(
        reader=InMemoryAnalysisReader(equity={run_id: equity}),
        metrics_repo=metrics_repo,
        objectives={
            "growth": ObjectiveConfig(weights={"total_return": 1.0}),
            "strict": ObjectiveConfig(weights={"profit_factor": 1.0}, on_missing_metric="fail"),
        },
    )

    metrics = analyzer.analyze(run_id)

    by_name = {m.name: m.value for m in metrics}
    assert by_name[fitness_metric_name("growth")] == pytest.approx(0.0201)
    # 'strict' requires profit_factor (absent: no trades) -> skipped, not crashed
    assert fitness_metric_name("strict") not in by_name
    # fitness rows are persisted alongside the regular metrics
    assert metrics_repo.saved[run_id] == metrics


def test_analyzer_without_objectives_adds_no_fitness() -> None:
    run_id = uuid4()
    analyzer = BacktestAnalyzer(
        reader=InMemoryAnalysisReader(), metrics_repo=InMemoryBacktestMetricsRepository()
    )
    metrics = analyzer.analyze(run_id)
    assert all(not m.name.startswith("fitness_") for m in metrics)
