"""Two-sided CUSUM change detection on standardized log returns.

Tracks cumulative deviations beyond a drift allowance in both directions;
a high peak statistic means the return process has shifted — the current
regime is breaking, so directional evidence computed over the window should
be trusted less.

Stability maps the peak linearly from `calm_level` (fully stable) to
`alarm_level` (regime break). The defaults are calibrated for the peak
statistic over a 150-return window: i.i.d. noise alone peaks around 4
(median) to ~6 (p90), while a 1.5-sigma mean shift peaks above 7.
"""

import numpy as np
import numpy.typing as npt

from carcharoth.regime.features.base import RegimeFeature
from carcharoth.regime.models import Evidence

_EPS = 1e-12


class CusumFeature(RegimeFeature):
    name = "cusum"

    def __init__(
        self,
        drift: float = 0.5,
        calm_level: float = 4.0,
        alarm_level: float = 10.0,
        window: int = 150,
    ) -> None:
        if drift < 0:
            raise ValueError("drift must be >= 0")
        if not 0 <= calm_level < alarm_level:
            raise ValueError("calm_level must be >= 0 and below alarm_level")
        if window < 2:
            raise ValueError("window must be >= 2")
        self._drift = drift
        self._calm_level = calm_level
        self._alarm_level = alarm_level
        self._window = window

    def min_returns(self) -> int:
        return self._window

    def compute(self, log_returns: npt.NDArray[np.float64]) -> Evidence | None:
        if len(log_returns) < self.min_returns():
            return None
        window = log_returns[-self._window :]
        std = float(window.std(ddof=1))
        if std < _EPS:
            return Evidence(feature=self.name, value=0.0, stability=1.0)

        z = (window - window.mean()) / std
        s_pos = s_neg = peak = 0.0
        for value in z:
            s_pos = max(0.0, s_pos + float(value) - self._drift)
            s_neg = max(0.0, s_neg - float(value) - self._drift)
            peak = max(peak, s_pos, s_neg)

        stability = float(
            np.clip((self._alarm_level - peak) / (self._alarm_level - self._calm_level), 0.0, 1.0)
        )
        return Evidence(feature=self.name, value=peak, stability=stability)
