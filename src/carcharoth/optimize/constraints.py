"""Hard-constraint checks on a trial's metrics — optimizer-side only.

Constraints steer the search (a violating trial reports the penalty score
to the sampler); the run's persisted fitness stays the honest weighted
score, so constraint changes never affect stored run data.
"""

from collections.abc import Sequence

from carcharoth.config.optimize_config import ConstraintConfig
from carcharoth.domain.models import MetricValue


def violated_constraints(
    metrics: Sequence[MetricValue], constraints: Sequence[ConstraintConfig]
) -> list[str]:
    """Human-readable descriptions of every violated constraint; a
    constraint on a metric the run did not produce counts as violated."""
    by_name = {m.name: m.value for m in metrics if m.symbol is None}
    violations: list[str] = []
    for constraint in constraints:
        value = by_name.get(constraint.metric)
        if value is None:
            violations.append(f"{constraint.metric} missing from run metrics")
        elif constraint.min is not None and value < constraint.min:
            violations.append(f"{constraint.metric}={value:g} < min {constraint.min:g}")
        elif constraint.max is not None and value > constraint.max:
            violations.append(f"{constraint.metric}={value:g} > max {constraint.max:g}")
    return violations
