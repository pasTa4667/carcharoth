"""Replayable market data for backtests.

Preloaded with historical bars, this service exposes a movable simulated
"now" (`advance_to`). Each snapshot contains the trailing `lookback` bars at
or before the cursor plus a synthetic quote derived from the newest bar's
close (bid/ask = close -/+ half the configured spread).

Fill-model semantics: `as_of` is the signal bar's timestamp and orders fill
at that bar's close (plus spread/slippage in the broker) — a standard
close-of-bar model, mildly optimistic versus next-bar-open fills.
"""

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from datetime import datetime

from carcharoth.domain.errors import MarketDataError
from carcharoth.domain.models import Bar, BarSpec, MarketSnapshot, Quote
from carcharoth.interfaces.market_data import MarketDataService

_SYNTHETIC_QUOTE_SIZE = 100.0


class HistoricalMarketDataService(MarketDataService):
    def __init__(self, bars: Mapping[str, list[Bar]], spread_pct: float) -> None:
        self._bars = {
            symbol: sorted(symbol_bars, key=lambda bar: bar.timestamp)
            for symbol, symbol_bars in bars.items()
        }
        self._timestamps = {
            symbol: [bar.timestamp for bar in symbol_bars]
            for symbol, symbol_bars in self._bars.items()
        }
        self._spread_pct = spread_pct
        self._as_of: datetime | None = None

    def advance_to(self, as_of: datetime) -> None:
        """Move the simulated clock; snapshots then end at this instant."""
        self._as_of = as_of

    def timestamp_grid(self, start: datetime, end: datetime) -> list[datetime]:
        """Sorted union of all bar timestamps in [start, end] — the tick grid.
        Bars only exist during market sessions, so no market-hours logic is
        needed on top."""
        return sorted(
            {
                ts
                for timestamps in self._timestamps.values()
                for ts in timestamps
                if start <= ts <= end
            }
        )

    def latest_closes(self) -> dict[str, float]:
        """Newest close per symbol at or before the cursor (marks the broker)."""
        as_of = self._require_cursor()
        closes: dict[str, float] = {}
        for symbol, timestamps in self._timestamps.items():
            index = bisect_right(timestamps, as_of)
            if index > 0:
                closes[symbol] = self._bars[symbol][index - 1].close
        return closes

    def get_snapshot(self, symbols: Sequence[str], spec: BarSpec) -> MarketSnapshot:
        as_of = self._require_cursor()
        bars: dict[str, list[Bar]] = {}
        quotes: dict[str, Quote] = {}
        for symbol in symbols:
            timestamps = self._timestamps.get(symbol, [])
            index = bisect_right(timestamps, as_of)
            window = self._bars.get(symbol, [])[max(0, index - spec.lookback) : index]
            bars[symbol] = window
            if window:
                quotes[symbol] = self._synthetic_quote(symbol, window[-1], as_of)
        return MarketSnapshot(bars=bars, quotes=quotes, as_of=as_of)

    def _synthetic_quote(self, symbol: str, bar: Bar, as_of: datetime) -> Quote:
        half_spread = self._spread_pct / 2
        return Quote(
            symbol=symbol,
            timestamp=as_of,
            bid_price=bar.close * (1 - half_spread),
            ask_price=bar.close * (1 + half_spread),
            bid_size=_SYNTHETIC_QUOTE_SIZE,
            ask_size=_SYNTHETIC_QUOTE_SIZE,
        )

    def _require_cursor(self) -> datetime:
        if self._as_of is None:
            raise MarketDataError("backtest cursor not set; call advance_to() first")
        return self._as_of
