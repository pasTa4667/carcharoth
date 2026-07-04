from datetime import UTC, datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.models import Clock

from carcharoth.interfaces.clock import MarketClock


class AlpacaMarketClock(MarketClock):
    def __init__(self, client: TradingClient) -> None:
        self._client = client

    def _get_clock(self) -> Clock:
        clock = self._client.get_clock()
        assert isinstance(clock, Clock)
        return clock

    def is_open(self) -> bool:
        return self._get_clock().is_open

    def seconds_until_open(self) -> float:
        clock = self._get_clock()
        if clock.is_open:
            return 0.0
        return max(0.0, (clock.next_open - datetime.now(UTC)).total_seconds())
