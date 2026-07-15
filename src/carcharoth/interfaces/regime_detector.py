"""Contract for market regime detectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from carcharoth.domain.models import Bar
    from carcharoth.regime.models import RegimeAssessment


class RegimeDetector(ABC):
    """Classifies a symbol's market regime from its bar history.

    Implementations may keep per-symbol state (e.g. fitted models) between
    calls; a detector lives for exactly one run, so state never leaks
    across runs.
    """

    @abstractmethod
    def required_lookback(self) -> int:
        """Bars needed before assess() can emit an assessment."""

    @abstractmethod
    def assess(self, symbol: str, bars: Sequence[Bar]) -> RegimeAssessment | None:
        """Assess the symbol's regime, or None while warming up."""
