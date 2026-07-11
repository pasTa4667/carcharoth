"""In-process read-through cache for historical bars across trials.

The fetch window varies per trial: the warm-up prefix depends on the
trial's lookback/timeframe parameters. Exact-argument memoization would
therefore miss constantly, so the cache widens instead: a request outside
the cached window fetches the union window and re-slices, giving at most
one fetch per distinct timeframe plus rare widenings.
"""

import bisect
from collections.abc import Sequence
from datetime import datetime

from carcharoth.domain.models import Bar, Timeframe
from carcharoth.interfaces.optimization import BarsFetcher

_Key = tuple[tuple[str, ...], Timeframe]
_Entry = tuple[datetime, datetime, dict[str, list[Bar]]]


class BarsCache:
    """Wraps a BarsFetcher; implements BarsFetcher itself (``end`` exclusive)."""

    def __init__(self, fetch: BarsFetcher) -> None:
        self._fetch = fetch
        self._entries: dict[_Key, _Entry] = {}

    def __call__(
        self, symbols: Sequence[str], timeframe: Timeframe, start: datetime, end: datetime
    ) -> dict[str, list[Bar]]:
        key = (tuple(sorted(symbols)), timeframe)
        entry = self._entries.get(key)
        if entry is None or start < entry[0] or end > entry[1]:
            fetch_start = start if entry is None else min(start, entry[0])
            fetch_end = end if entry is None else max(end, entry[1])
            bars = self._fetch(symbols, timeframe, fetch_start, fetch_end)
            entry = (fetch_start, fetch_end, bars)
            self._entries[key] = entry
        return _slice(entry[2], start, end)


def _slice(
    bars_by_symbol: dict[str, list[Bar]], start: datetime, end: datetime
) -> dict[str, list[Bar]]:
    """Bars with ``start <= timestamp < end``; bars are sorted oldest-first."""
    result: dict[str, list[Bar]] = {}
    for symbol, bars in bars_by_symbol.items():
        timestamps = [bar.timestamp for bar in bars]
        lo = bisect.bisect_left(timestamps, start)
        hi = bisect.bisect_left(timestamps, end)
        result[symbol] = bars[lo:hi]
    return result
