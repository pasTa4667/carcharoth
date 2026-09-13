"""Monthly v2 bars cache with self-contained fetched coverage.

Single-key last-writer-wins is deliberate: stale writers can reduce hit rate,
never attach another writer's coverage to unrelated rows. v1 is untouched.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from carcharoth.domain.models import Bar, Timeframe
from carcharoth.interfaces.cache import ByteStore
from carcharoth.interfaces.optimization import BarsFetcher
from carcharoth.performance import increment, stage
from carcharoth.services.cache.bars_codec import Chunk, Interval, decode, encode, union

logger = logging.getLogger(__name__)
BARS_PREFIX = "carch:bars:v2:"
V1_BARS_PREFIX = "carch:bars:v1:"
_BATCH_SIZE = 16


def bars_key(timeframe: Timeframe, symbol: str, month: str) -> str:
    return f"{BARS_PREFIX}{timeframe.amount}{timeframe.unit.value}:{symbol}:{month}"


def _next_month(value: datetime) -> datetime:
    return value.replace(year=value.year + (value.month == 12), month=value.month % 12 + 1)


def _missing(coverage: Sequence[Interval], start: datetime, end: datetime) -> list[Interval]:
    result = []
    cursor = start
    for a, b in coverage:
        if cursor < min(a, end):
            result.append((cursor, min(a, end)))
        cursor = max(cursor, b)
        if cursor >= end:
            break
    if cursor < end:
        result.append((cursor, end))
    return result


def _normalize(bars: Sequence[Bar], symbol: str, start: datetime, end: datetime) -> list[Bar]:
    rows: dict[datetime, Bar] = {}
    for bar in bars:
        if bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None:
            raise ValueError("upstream bars require aware timestamps")
        if bar.symbol != symbol:
            raise ValueError("upstream bar symbol mismatch")
        if start <= bar.timestamp < end:
            rows[bar.timestamp] = bar
    return sorted(rows.values(), key=lambda bar: bar.timestamp)


class PersistentBarsCache:
    """BarsFetcher with half-open UTC ranges and bounded monthly decode batches."""

    def __init__(
        self,
        store: ByteStore,
        fetch: BarsFetcher,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store, self._fetch, self._now = store, fetch, now

    def __call__(
        self, symbols: Sequence[str], timeframe: Timeframe, start: datetime, end: datetime
    ) -> dict[str, list[Bar]]:
        if any(t.tzinfo is None or t.utcoffset() is None for t in (start, end)):
            raise ValueError("bars cache requires aware datetimes")
        if end < start:
            raise ValueError("bars cache end precedes start")
        symbols = list(dict.fromkeys(symbols))
        result: dict[str, list[Bar]] = {s: [] for s in symbols}
        if not symbols or start == end:
            return result
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        today = self._now().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        cache_end = min(end, today)
        requests: list[tuple[str, datetime, datetime]] = []
        month = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while start < cache_end and month < cache_end:
            next_month = _next_month(month)
            requests.extend((symbol, month, next_month) for symbol in symbols)
            month = next_month
        warned = False

        def warn() -> None:
            nonlocal warned
            if not warned:
                logger.warning("bars cache v2: invalid payload/codec failure; bypassing storage")
                warned = True

        tf = f"{timeframe.amount}{timeframe.unit.value}"
        for offset in range(0, len(requests), _BATCH_SIZE):
            batch = requests[offset : offset + _BATCH_SIZE]
            keys = [bars_key(timeframe, s, a.strftime("%Y-%m")) for s, a, _ in batch]
            with stage("cache_read"):
                payloads = self._store.mget(keys)
            entries: list[Chunk] = []
            missing: dict[str, list[Interval]] = {}
            with stage("cache_decode"):
                for (symbol, a, b), payload in zip(batch, payloads, strict=True):
                    chunk = Chunk.empty()
                    if payload is not None:
                        increment("cache_bytes_read", len(payload))
                        try:
                            chunk = decode(payload, symbol, tf, a.strftime("%Y-%m"), a, b)
                            increment("cache_bytes_decoded", chunk.nbytes)
                            chunk = chunk.before(today)
                        except Exception:
                            warn()
                    gaps = _missing(chunk.coverage, max(a, start), min(b, cache_end))
                    increment("cache_chunk_misses" if gaps else "cache_chunk_hits")
                    missing.setdefault(symbol, []).extend(gaps)
                    entries.append(chunk)
            groups: dict[Interval, list[str]] = {}
            for symbol, gaps in missing.items():
                for gap in union(gaps):
                    groups.setdefault(gap, []).append(symbol)
            changed: set[int] = set()
            for (a, b), group in groups.items():
                with stage("cache_fetch"):
                    fetched = self._fetch(group, timeframe, a, b)
                for i, (symbol, month_start, month_end) in enumerate(batch):
                    left, right = max(a, month_start), min(b, month_end)
                    if symbol in group and left < right:
                        fresh = _normalize(fetched.get(symbol, []), symbol, left, right)
                        entries[i] = entries[i].merge(fresh, (left, right))
                        changed.add(i)
            with stage("cache_write"):
                writes = {}
                for i in changed:
                    symbol, a, _ = batch[i]
                    try:
                        writes[keys[i]] = encode(entries[i], symbol, tf, a.strftime("%Y-%m"))
                    except Exception:
                        warn()
                if writes:
                    increment("cache_bytes_written", sum(map(len, writes.values())))
                    self._store.mset(writes)
            for (symbol, a, b), chunk in zip(batch, entries, strict=True):
                result[symbol].extend(chunk.bars(symbol, max(a, start), min(b, cache_end)))
        if end > max(start, cache_end):
            tail_start = max(start, cache_end)
            with stage("cache_fetch"):
                tail = self._fetch(symbols, timeframe, tail_start, end)
            for symbol in symbols:
                result[symbol].extend(_normalize(tail.get(symbol, []), symbol, tail_start, end))
        return {s: _normalize(rows, s, start, end) for s, rows in result.items()}
