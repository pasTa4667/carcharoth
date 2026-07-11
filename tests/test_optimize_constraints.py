"""Hard-constraint checks on trial metrics."""

from carcharoth.config.optimize_config import ConstraintConfig
from carcharoth.domain.models import MetricValue
from carcharoth.optimize.constraints import violated_constraints

METRICS = [
    MetricValue("num_trades", 15.0),
    MetricValue("max_drawdown", 0.25),
    MetricValue("symbol_pnl", 100.0, symbol="AAPL"),
]


def test_feasible_metrics_pass() -> None:
    constraints = [
        ConstraintConfig(metric="num_trades", min=10),
        ConstraintConfig(metric="max_drawdown", max=0.30),
    ]
    assert violated_constraints(METRICS, constraints) == []


def test_min_violation_detected() -> None:
    violations = violated_constraints(METRICS, [ConstraintConfig(metric="num_trades", min=20)])
    assert violations == ["num_trades=15 < min 20"]


def test_max_violation_detected() -> None:
    violations = violated_constraints(METRICS, [ConstraintConfig(metric="max_drawdown", max=0.1)])
    assert violations == ["max_drawdown=0.25 > max 0.1"]


def test_missing_metric_counts_as_violation() -> None:
    violations = violated_constraints(METRICS, [ConstraintConfig(metric="profit_factor", min=1.0)])
    assert violations == ["profit_factor missing from run metrics"]


def test_symbol_metrics_are_not_matched() -> None:
    violations = violated_constraints(METRICS, [ConstraintConfig(metric="symbol_pnl", min=1.0)])
    assert violations == ["symbol_pnl missing from run metrics"]


def test_no_constraints_no_violations() -> None:
    assert violated_constraints(METRICS, []) == []
