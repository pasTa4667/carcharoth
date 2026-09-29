"""Feature registry contracts internal to the model pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import numpy.typing as npt

from carcharoth.domain.models import Bar, Timeframe


@dataclass(frozen=True, slots=True)
class FeatureContext:
    bars: tuple[Bar, ...]
    benchmark_bars: tuple[Bar, ...]
    timeframe: Timeframe

    @property
    def as_of(self) -> datetime:
        return self.bars[-1].timestamp


class Feature(ABC):
    """One scalar, versioned feature definition."""

    implementation_version: int = 1
    requires_benchmark: bool = False

    @property
    @abstractmethod
    def output_name(self) -> str:
        """Stable column name written to the artifact schema."""

    @abstractmethod
    def required_bars(self, timeframe: Timeframe) -> int:
        """Conservative regular-session history required by this feature."""

    @abstractmethod
    def compute(self, context: FeatureContext) -> float | None:
        """Latest scalar value, or None while unavailable/warming up."""

    def compute_series(
        self,
        bars: tuple[Bar, ...],
        benchmark_bars: tuple[Bar, ...],
        timeframe: Timeframe,
    ) -> npt.NDArray[np.float64]:
        """One value per bar; custom features may use this correct fallback.

        Built-ins override it with vectorized/linear implementations so global
        dataset construction does not recompute every prefix.
        """
        result = np.full(len(bars), np.nan, dtype=np.float64)
        benchmark_end = 0
        for index in range(len(bars)):
            while (
                benchmark_end < len(benchmark_bars)
                and benchmark_bars[benchmark_end].timestamp <= bars[index].timestamp
            ):
                benchmark_end += 1
            context = FeatureContext(bars[: index + 1], benchmark_bars[:benchmark_end], timeframe)
            value = self.compute(context)
            if value is not None:
                result[index] = value
        return result
