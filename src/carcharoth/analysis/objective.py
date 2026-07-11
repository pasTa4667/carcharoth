"""Fitness scoring: weighted composite over analyzer metrics — pure, no I/O.

Objectives are named and defined in the base config (``objectives:``), so
every run carries its own fitness score(s) independent of what launched it.
"""

from collections.abc import Sequence

from carcharoth.config.app_config import ObjectiveConfig
from carcharoth.domain.models import MetricValue

#: Prefix of the per-objective fitness metric persisted with each run.
FITNESS_PREFIX = "fitness_"


class MissingMetricError(ValueError):
    """A weighted metric is absent and the objective's policy is ``fail``."""


def fitness_metric_name(objective_name: str) -> str:
    return f"{FITNESS_PREFIX}{objective_name}"


def score_metrics(metrics: Sequence[MetricValue], objective: ObjectiveConfig) -> float:
    """Weighted sum over portfolio-level metrics; per-symbol metrics are ignored."""
    by_name = {m.name: m.value for m in metrics if m.symbol is None}
    missing = sorted(set(objective.weights) - set(by_name))
    if missing:
        if objective.on_missing_metric == "fail":
            raise MissingMetricError(f"metrics missing for objective: {missing}")
        if objective.on_missing_metric == "penalize":
            return objective.penalty_score
        # "zero": missing metrics contribute nothing
    return sum(
        weight * by_name[name] for name, weight in objective.weights.items() if name in by_name
    )
