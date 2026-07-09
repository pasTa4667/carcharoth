"""Contract for regime evidence features.

Features are pure functions over a log-returns array, mirroring
strategies/indicators.py: no I/O, and None during warm-up. The detector
converts bars to log returns once, so features stay numpy-only.
"""

from abc import ABC, abstractmethod

import numpy as np
import numpy.typing as npt

from carcharoth.regime.models import Evidence


class RegimeFeature(ABC):
    name: str

    @abstractmethod
    def min_returns(self) -> int:
        """Minimum number of log returns required to emit evidence."""

    @abstractmethod
    def compute(self, log_returns: npt.NDArray[np.float64]) -> Evidence | None:
        """Return this feature's evidence, or None during warm-up."""
