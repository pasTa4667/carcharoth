from abc import ABC, abstractmethod


class MarketClock(ABC):
    """Answers whether the market is currently open for trading."""

    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    def seconds_until_open(self) -> float:
        """Seconds until the next market open; 0 if the market is open now."""
