"""One-shot historical bar fetches for backtesting.

Shares the fetch-window heuristics with the live market data service so the
warm-up a backtest prefetches matches what strategies see in live trading.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from carcharoth.domain.errors import MarketDataError
from carcharoth.domain.models import Bar, BarSpec, Timeframe, TimeframeUnit
from carcharoth.services.alpaca.mappers import to_bar

# Fetch a generous window so weekends/closed hours still yield enough bars;
# 8x a 205-bar 5-minute lookback spans ~5.7 days, covering a 3-day weekend.
_MINUTE_WINDOW_MULTIPLIER = 8

_UNIT_MAP = {
    TimeframeUnit.MINUTE: TimeFrameUnit.Minute,
    TimeframeUnit.DAY: TimeFrameUnit.Day,
}


def to_alpaca_timeframe(timeframe: Timeframe) -> TimeFrame:
    return TimeFrame(timeframe.amount, _UNIT_MAP[timeframe.unit])


def warmup_window(spec: BarSpec) -> timedelta:
    """Calendar time to fetch before a point so `spec.lookback` bars exist."""
    if spec.timeframe.unit is TimeframeUnit.MINUTE:
        minutes = spec.timeframe.amount * spec.lookback * _MINUTE_WINDOW_MULTIPLIER
        return timedelta(minutes=minutes)
    # Trading days -> calendar days: ~5 trading days per 7 calendar days;
    # 2x plus padding is generous and daily bars are cheap to over-fetch.
    return timedelta(days=spec.lookback * 2 + 5)


def fetch_historical_bars(
    client: StockHistoricalDataClient,
    symbols: Sequence[str],
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
) -> dict[str, list[Bar]]:
    """All bars per symbol in [start, end], oldest first; the SDK paginates."""
    request = StockBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=to_alpaca_timeframe(timeframe),
        start=start,
        end=end,
    )
    try:
        response = client.get_stock_bars(request)
    except Exception as exc:
        raise MarketDataError(f"failed to fetch historical bars: {exc}") from exc
    assert not isinstance(response, dict)  # raw feed is off by default
    bars = {
        symbol: sorted((to_bar(bar) for bar in symbol_bars), key=lambda b: b.timestamp)
        for symbol, symbol_bars in response.data.items()
    }
    # Symbols with no bars at all still get an (empty) entry.
    for symbol in symbols:
        bars.setdefault(symbol, [])
    return bars
