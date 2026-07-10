from collections.abc import Sequence
from datetime import UTC, datetime

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest

from carcharoth.domain.errors import MarketDataError
from carcharoth.domain.models import Bar, BarSpec, MarketSnapshot, Quote
from carcharoth.interfaces.cache import Cache
from carcharoth.interfaces.market_data import MarketDataService
from carcharoth.services.alpaca.historical import to_alpaca_timeframe, warmup_window
from carcharoth.services.alpaca.mappers import to_bar, to_quote
from carcharoth.services.cache.noop import NoOpCache

_BARS_CACHE_TTL_SECONDS = 30.0


class AlpacaMarketDataService(MarketDataService):
    def __init__(self, client: StockHistoricalDataClient, cache: Cache | None = None) -> None:
        self._client = client
        self._cache = cache if cache is not None else NoOpCache()

    def get_snapshot(self, symbols: Sequence[str], spec: BarSpec) -> MarketSnapshot:
        as_of = datetime.now(UTC)
        try:
            bars = self._get_bars(list(symbols), spec, as_of)
            quotes = self._get_quotes(list(symbols))
        except Exception as exc:
            raise MarketDataError(f"failed to fetch market data: {exc}") from exc
        return MarketSnapshot(bars=bars, quotes=quotes, as_of=as_of)

    def _get_bars(self, symbols: list[str], spec: BarSpec, as_of: datetime) -> dict[str, list[Bar]]:
        timeframe = spec.timeframe
        cache_key = (
            f"bars:{','.join(sorted(symbols))}"
            f":{timeframe.amount}:{timeframe.unit.value}:{spec.lookback}"
        )
        cached: dict[str, list[Bar]] | None = self._cache.get(cache_key)
        if cached is not None:
            return cached

        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=to_alpaca_timeframe(timeframe),
            start=as_of - warmup_window(spec),
        )
        response = self._client.get_stock_bars(request)
        assert not isinstance(response, dict)  # raw feed is off by default
        bars = {
            symbol: [to_bar(bar) for bar in symbol_bars][-spec.lookback :]
            for symbol, symbol_bars in response.data.items()
        }
        # Symbols with no bars at all still get an (empty) entry.
        for symbol in symbols:
            bars.setdefault(symbol, [])
        self._cache.set(cache_key, bars, _BARS_CACHE_TTL_SECONDS)
        return bars

    def _get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        request = StockLatestQuoteRequest(symbol_or_symbols=symbols)
        response = self._client.get_stock_latest_quote(request)
        return {symbol: to_quote(quote) for symbol, quote in response.items()}
