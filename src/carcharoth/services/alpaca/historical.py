"""One-shot historical bar fetches for backtesting.

Shares the fetch-window heuristics with the live market data service so the
warm-up a backtest prefetches matches what strategies see in live trading.
"""

import math
from collections.abc import Sequence
from datetime import datetime, timedelta

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from carcharoth.domain.errors import MarketDataError
from carcharoth.domain.models import Bar, BarSpec, Timeframe, TimeframeUnit
from carcharoth.services.alpaca.mappers import to_bar

# Regular US session: 9:30-16:00. Minute bars only accrue during sessions,
# so the fetch window is sized in trading days and converted to calendar
# days (~5 trading days per 7 calendar days) plus padding for holidays and
# long weekends.
_REGULAR_SESSION_MINUTES = 390
_CALENDAR_PADDING_DAYS = 5

_UNIT_MAP = {
    TimeframeUnit.MINUTE: TimeFrameUnit.Minute,
    TimeframeUnit.DAY: TimeFrameUnit.Day,
}


def to_alpaca_timeframe(timeframe: Timeframe) -> TimeFrame:
    return TimeFrame(timeframe.amount, _UNIT_MAP[timeframe.unit])


def warmup_window(spec: BarSpec) -> timedelta:
    """Calendar time to fetch before a point so `spec.lookback` bars exist."""
    if spec.timeframe.unit is TimeframeUnit.MINUTE:
        trading_days = math.ceil(spec.timeframe.amount * spec.lookback / _REGULAR_SESSION_MINUTES)
        calendar_days = math.ceil(trading_days * 7 / 5) + _CALENDAR_PADDING_DAYS
        return timedelta(days=calendar_days)
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
