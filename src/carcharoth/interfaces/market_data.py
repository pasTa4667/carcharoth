from abc import ABC, abstractmethod
from collections.abc import Sequence

from carcharoth.domain.models import MarketSnapshot


class MarketDataService(ABC):
    """Provides market data (bars, quotes) for the watchlist symbols."""

    @abstractmethod
    def get_snapshot(
        self, symbols: Sequence[str], timeframe_minutes: int, lookback: int
    ) -> MarketSnapshot:
        """Return up to `lookback` bars (newest last) plus the latest quote per symbol.

        Symbols with insufficient history are returned with whatever bars exist;
        deciding what to do with short history is the strategy's job.

        Raises MarketDataError if data cannot be fetched at all.
        """
