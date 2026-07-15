"""Persistent cross-run cache for historical bars.

Wraps any BarsFetcher; one ByteStore entry per (timeframe, symbol) holding a
single contiguous coverage window. Requests are gap-filled: only missing
head/tail ranges are fetched upstream (grouped so symbols sharing a range go
out as one batched call). Coverage never extends into the current UTC day —
partial intraday data is re-fetched fresh on every call instead of going
stale in the cache (at the cost of one extra upstream call for windows that
touch today).

Payloads are pickles; a corrupt or foreign value is treated as a miss and
overwritten. A disjoint older request bridges the gap by construction (the
missing head range spans from the new start to the old coverage start), an
accepted over-fetch that keeps every entry a single interval.
"""

import bisect
import logging
import pickle
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from carcharoth.domain.models import Bar, Timeframe
from carcharoth.interfaces.cache import ByteStore
from carcharoth.interfaces.optimization import BarsFetcher

logger = logging.getLogger(__name__)

BARS_PREFIX = "carch:bars:v1:"


def bars_key(timeframe: Timeframe, symbol: str) -> str:
    return f"{BARS_PREFIX}{timeframe.amount}{timeframe.unit.value}:{symbol}"


@dataclass(frozen=True, slots=True)
class CachedBars:
    """One symbol's contiguous coverage window; the pickled cache payload."""

    coverage_start: datetime
    coverage_end: datetime  # exclusive; never inside the current UTC day
    bars: list[Bar]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PersistentBarsCache:
    """Wraps a BarsFetcher; implements BarsFetcher itself (``end`` exclusive)."""

    def __init__(
        self,
        store: ByteStore,
        fetch: BarsFetcher,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store = store
        self._fetch = fetch
        self._now = now

    def __call__(
        self, symbols: Sequence[str], timeframe: Timeframe, start: datetime, end: datetime
    ) -> dict[str, list[Bar]]:
        today = self._now().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        cacheable_end = min(end, today)
        if cacheable_end <= start:
            return self._fetch(symbols, timeframe, start, end)  # whole window is today

        entries = self._load(symbols, timeframe)
        groups: dict[tuple[datetime, datetime], list[str]] = {}
        for symbol in symbols:
            for window in _missing_ranges(entries.get(symbol), start, cacheable_end):
                groups.setdefault(window, []).append(symbol)
        if groups:
            logger.info(
                "bars cache: fetching %d missing range(s) covering %d symbol(s)",
                len(groups),
                len({symbol for group in groups.values() for symbol in group}),
            )
        else:
            logger.info("bars cache: all %d symbol(s) served from cache", len(symbols))

        changed: dict[str, CachedBars] = {}
        for (fetch_start, fetch_end), group in groups.items():
            fetched = self._fetch(group, timeframe, fetch_start, fetch_end)
            for symbol in group:
                merged = _merge(
                    entries.get(symbol), fetched.get(symbol, []), fetch_start, fetch_end
                )
                entries[symbol] = merged
                changed[bars_key(timeframe, symbol)] = merged
        if changed:
            self._store.mset(
                {key: pickle.dumps(entry, protocol=5) for key, entry in changed.items()}
            )

        result = {
            symbol: _slice_bars(entries[symbol].bars, start, cacheable_end) for symbol in symbols
        }
        if end > cacheable_end:  # fresh tail inside today: returned but never stored
            tail = self._fetch(symbols, timeframe, cacheable_end, end)
            for symbol in symbols:
                result[symbol] += _slice_bars(tail.get(symbol, []), cacheable_end, end)
        return result

    def _load(self, symbols: Sequence[str], timeframe: Timeframe) -> dict[str, CachedBars]:
        payloads = self._store.mget([bars_key(timeframe, symbol) for symbol in symbols])
        entries: dict[str, CachedBars] = {}
        for symbol, payload in zip(symbols, payloads, strict=True):
            entry = _decode(payload)
            if entry is not None:
                entries[symbol] = entry
        return entries


def _decode(payload: bytes | None) -> CachedBars | None:
    """Unpickle a payload; anything corrupt or foreign is a miss."""
    if payload is None:
        return None
    try:
        entry = pickle.loads(payload)  # self-produced values, local redis only
    except Exception:
        logger.warning("bars cache: dropping unreadable payload", exc_info=True)
        return None
    return entry if isinstance(entry, CachedBars) else None


def _missing_ranges(
    entry: CachedBars | None, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    if entry is None:
        return [(start, end)]
    ranges = []
    if start < entry.coverage_start:
        ranges.append((start, entry.coverage_start))
    if end > entry.coverage_end:
        ranges.append((entry.coverage_end, end))
    return ranges


def _merge(
    entry: CachedBars | None, fetched: list[Bar], fetch_start: datetime, fetch_end: datetime
) -> CachedBars:
    """Widened coverage; freshly fetched bars win on timestamp collisions.
    An empty fetch still widens — "we asked, there was nothing" is cached."""
    if entry is None:
        return CachedBars(fetch_start, fetch_end, sorted(fetched, key=lambda b: b.timestamp))
    merged = {bar.timestamp: bar for bar in entry.bars}
    merged.update({bar.timestamp: bar for bar in fetched})
    return CachedBars(
        coverage_start=min(entry.coverage_start, fetch_start),
        coverage_end=max(entry.coverage_end, fetch_end),
        bars=sorted(merged.values(), key=lambda b: b.timestamp),
    )


def _slice_bars(bars: list[Bar], start: datetime, end: datetime) -> list[Bar]:
    """Bars with ``start <= timestamp < end``; bars are sorted oldest-first."""
    timestamps = [bar.timestamp for bar in bars]
    return bars[bisect.bisect_left(timestamps, start) : bisect.bisect_left(timestamps, end)]
