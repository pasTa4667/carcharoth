"""Distribution shift between recent and reference return windows.

The 1-D Wasserstein (earth mover's) distance between the last
`recent_window` returns and the `reference_window` returns before them,
normalized by the reference standard deviation so the value is comparable
across symbols regardless of their volatility level.

Stability maps the distance linearly from `calm_level` (fully stable) to
`alarm_level` (regime break). Defaults are calibrated for 60-vs-180
windows: pure sampling noise between identical distributions measures
~0.17 (median) to ~0.27 (p90) sigma, while a tripled volatility measures
above 1.2 sigma.
"""

import numpy as np
import numpy.typing as npt
from scipy.stats import wasserstein_distance

from carcharoth.regime.features.base import RegimeFeature
from carcharoth.regime.models import Evidence

_EPS = 1e-12


class WassersteinFeature(RegimeFeature):
    name = "wasserstein"

    def __init__(
        self,
        recent_window: int = 60,
        reference_window: int = 180,
        calm_level: float = 0.2,
        alarm_level: float = 1.0,
    ) -> None:
        if recent_window < 2 or reference_window < 2:
            raise ValueError("windows must be >= 2")
        if not 0 <= calm_level < alarm_level:
            raise ValueError("calm_level must be >= 0 and below alarm_level")
        self._recent_window = recent_window
        self._reference_window = reference_window
        self._calm_level = calm_level
        self._alarm_level = alarm_level

    def min_returns(self) -> int:
        return self._recent_window + self._reference_window

    def compute(self, log_returns: npt.NDArray[np.float64]) -> Evidence | None:
        if len(log_returns) < self.min_returns():
            return None
        recent = log_returns[-self._recent_window :]
        reference = log_returns[-self.min_returns() : -self._recent_window]
        sigma = float(reference.std(ddof=1))
        if sigma < _EPS:
            return Evidence(feature=self.name, value=0.0, stability=1.0)

        distance = float(wasserstein_distance(recent / sigma, reference / sigma))
        stability = float(
            np.clip(
                (self._alarm_level - distance) / (self._alarm_level - self._calm_level),
                0.0,
                1.0,
            )
        )
        return Evidence(feature=self.name, value=distance, stability=stability)
