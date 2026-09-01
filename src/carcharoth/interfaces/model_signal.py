"""The engine seam for model-driven exposure decisions."""

from abc import ABC, abstractmethod

from carcharoth.domain.models import (
    ExposureIntent,
    MarketDataRequirements,
    MarketSnapshot,
    Position,
)


class ModelSignalGenerator(ABC):
    """Turns a market snapshot into a target exposure for one symbol.

    Implementations own feature assembly, model inference, completed-bar
    de-duplication and the position-aware decision policy.
    """

    @abstractmethod
    def market_requirements(self) -> MarketDataRequirements:
        """Bars and non-tradable context symbols needed for one decision."""

    @abstractmethod
    def generate(
        self,
        symbol: str,
        snapshot: MarketSnapshot,
        position: Position | None,
    ) -> ExposureIntent:
        """Return LONG/SHORT/FLAT/UNCHANGED for ``symbol``."""
