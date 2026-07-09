from abc import ABC, abstractmethod
from collections.abc import Sequence

from carcharoth.domain.models import BarSpec, MarketSnapshot


class MarketDataService(ABC):
    """Provides market data (bars, quotes) for the watchlist symbols."""

    @abstractmethod
    def get_snapshot(self, symbols: Sequence[str], spec: BarSpec) -> MarketSnapshot:
        """Return up to `spec.lookback` bars of `spec.timeframe` resolution
        (newest last) plus the latest quote per symbol.

        Symbols with insufficient history are returned with whatever bars exist;
        deciding what to do with short history is the strategy's job.

        Raises MarketDataError if data cannot be fetched at all.
        """
