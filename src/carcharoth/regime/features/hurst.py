"""Hurst exponent via Anis-Lloyd-corrected rescaled-range (R/S) analysis.

H > 0.5 indicates persistence (trending), H < 0.5 anti-persistence (mean
reversion). Raw R/S is biased upward on small windows (uncorrected, even a
strongly mean-reverting series reads H > 0.5), so the slope is fit on
log(R/S) - log(E[R/S]) where E[R/S] is the Anis-Lloyd expected value under
the random-walk null, and H = 0.5 + slope.
"""

import numpy as np
import numpy.typing as npt
from scipy.special import gammaln

from carcharoth.regime.features.base import RegimeFeature
from carcharoth.regime.models import Evidence

_MIN_BLOCK_SIZES = 3
_EPS = 1e-12


class HurstFeature(RegimeFeature):
    name = "hurst"

    def __init__(self, min_window: int = 8, scale: float = 0.2) -> None:
        if min_window < 4:
            raise ValueError("min_window must be >= 4")
        if scale <= 0:
            raise ValueError("scale must be > 0")
        self._min_window = min_window
        self._scale = scale

    def min_returns(self) -> int:
        # smallest N where {w, 2w, 4w} all fit with n <= N // 2
        return self._min_window * 8

    def compute(self, log_returns: npt.NDArray[np.float64]) -> Evidence | None:
        n_total = len(log_returns)
        if n_total < self.min_returns():
            return None

        sizes: list[int] = []
        size = self._min_window
        while size <= n_total // 2:
            sizes.append(size)
            size *= 2

        log_n: list[float] = []
        excess_log_rs: list[float] = []
        for n in sizes:
            rs = _mean_rescaled_range(log_returns, n)
            if rs is None:
                continue
            log_n.append(np.log(n))
            excess_log_rs.append(np.log(rs) - np.log(_expected_rescaled_range(n)))
        if len(log_n) < _MIN_BLOCK_SIZES:
            return None

        hurst = 0.5 + float(np.polyfit(log_n, excess_log_rs, 1)[0])
        direction = float(np.clip((hurst - 0.5) / self._scale, -1.0, 1.0))
        return Evidence(feature=self.name, value=hurst, direction=direction)


def _mean_rescaled_range(returns: npt.NDArray[np.float64], n: int) -> float | None:
    """Mean R/S over all disjoint blocks of length n; None if no block has
    usable variance (e.g. constant prices)."""
    n_blocks = len(returns) // n
    ratios: list[float] = []
    for i in range(n_blocks):
        block = returns[i * n : (i + 1) * n]
        std = float(block.std(ddof=1))
        if std < _EPS:
            continue
        deviations = np.cumsum(block - block.mean())
        rescaled = float((deviations.max() - deviations.min()) / std)
        if rescaled > _EPS:
            ratios.append(rescaled)
    if not ratios:
        return None
    return float(np.mean(ratios))


def _expected_rescaled_range(n: int) -> float:
    """Anis-Lloyd expected R/S for i.i.d. Gaussian noise, with Peters'
    finite-sample factor (n - 0.5) / n."""
    i = np.arange(1, n)
    tail_sum = float(np.sqrt((n - i) / i).sum())
    if n <= 340:
        factor = float(np.exp(gammaln((n - 1) / 2) - gammaln(n / 2))) / np.sqrt(np.pi)
    else:
        # gamma-ratio approximation, avoids overflow for large n
        factor = 1.0 / np.sqrt(n * np.pi / 2)
    return float((n - 0.5) / n * factor * tail_sum)
