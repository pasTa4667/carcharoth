"""Volatility clustering via autocorrelation of squared returns.

Strong clustering (GARCH-like bursty volatility) marks an overreaction /
reversal environment, so it contributes weak evidence toward mean reversion.
The polarity is a tunable prior — give it a low weight in config.
"""

import numpy as np
import numpy.typing as npt

from carcharoth.regime.features.base import RegimeFeature
from carcharoth.regime.models import Evidence

_EPS = 1e-12


class VolClusteringFeature(RegimeFeature):
    name = "vol_clustering"

    def __init__(self, max_lag: int = 5, window: int = 150, scale: float = 0.3) -> None:
        if max_lag < 1:
            raise ValueError("max_lag must be >= 1")
        if window <= max_lag + 1:
            raise ValueError("window must exceed max_lag + 1")
        if scale <= 0:
            raise ValueError("scale must be > 0")
        self._max_lag = max_lag
        self._window = window
        self._scale = scale

    def min_returns(self) -> int:
        return self._window

    def compute(self, log_returns: npt.NDArray[np.float64]) -> Evidence | None:
        if len(log_returns) < self.min_returns():
            return None
        squared = log_returns[-self._window :] ** 2
        if float(squared.std()) < _EPS:
            clustering = 0.0
        else:
            # negative autocorrelation of squared returns is noise, clip at 0
            clustering = float(
                np.mean([max(_autocorr(squared, lag), 0.0) for lag in range(1, self._max_lag + 1)])
            )
        direction = -min(clustering / self._scale, 1.0)
        return Evidence(feature=self.name, value=clustering, direction=direction)


def _autocorr(x: npt.NDArray[np.float64], lag: int) -> float:
    a, b = x[lag:], x[:-lag]
    if float(a.std()) < _EPS or float(b.std()) < _EPS:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])
