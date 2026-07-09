"""Chooses which Strategy evaluates a given symbol each tick.

Unlike Strategy, providers are not pure: regime-aware implementations keep
per-symbol state and persist their decisions, like repositories do.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from carcharoth.domain.models import Bar, BarSpec, Position
from carcharoth.interfaces.strategy import Strategy


class StrategyProvider(ABC):
    @abstractmethod
    def required_bars(self) -> BarSpec:
        """The combined bar requirement of everything this provider runs
        (all managed strategies plus any detector of its own)."""

    @abstractmethod
    def resolve(
        self,
        symbol: str,
        bars: list[Bar],
        position: Position | None,
        as_of: datetime,
    ) -> Strategy:
        """Return the strategy that trades `symbol` this tick."""
