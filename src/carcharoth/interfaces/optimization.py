"""Contracts for parameter optimization.

The optimizer is deliberately agnostic: it consumes a ``BacktestFunc``
callable (satisfied by a closure built in the composition root) and reads
the fitness metric each run's analysis already produced. It never touches
the broker, the database, or strategy code.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from carcharoth.config.app_config import AppConfig
from carcharoth.domain.models import BacktestResult, Bar, OptimizationResult, Timeframe


class BarsFetcher(Protocol):
    """Fetches historical bars for a window; ``end`` is exclusive."""

    def __call__(
        self, symbols: Sequence[str], timeframe: Timeframe, start: datetime, end: datetime
    ) -> dict[str, list[Bar]]: ...


class BacktestFunc(Protocol):
    """Runs one fully-persisted backtest and returns its run id + metrics."""

    def __call__(
        self,
        config: AppConfig,
        start: datetime,
        end_exclusive: datetime,
        symbols: Sequence[str],
    ) -> BacktestResult: ...


class ParameterOptimizer(ABC):
    @abstractmethod
    def optimize(self) -> OptimizationResult:
        """Run the study to completion and return its outcome."""
