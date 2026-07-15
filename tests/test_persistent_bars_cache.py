"""Persistent bars cache: gap-fill, grouping, today-clamp, corrupt payloads."""

import pickle
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from carcharoth.domain.models import Bar, Timeframe
from carcharoth.services.cache.bars import CachedBars, PersistentBarsCache, bars_key
from tests.fakes import InMemoryByteStore

T0 = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
NOW = datetime(2026, 6, 10, 15, 0, tzinfo=UTC)  # clamp -> 2026-06-10 00:00 UTC
MIDNIGHT = datetime(2026, 6, 10, tzinfo=UTC)
TF_5M = Timeframe.minutes(5)


def bar(symbol: str, minute: int) -> Bar:
    ts = T0 + timedelta(minutes=minute)
    return Bar(symbol=symbol, timestamp=ts, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)


class RecordingFetcher:
    """Serves 5-minute bars for the requested window; records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], datetime, datetime]] = []

    def __call__(
        self, symbols: Sequence[str], timeframe: Timeframe, start: datetime, end: datetime
    ) -> dict[str, list[Bar]]:
        self.calls.append((tuple(symbols), start, end))
        minutes = range(0, 14 * 24 * 60, 5)
        return {
            s: [bar(s, m) for m in minutes if start <= T0 + timedelta(minutes=m) < end]
            for s in symbols
        }


def make_cache(store: InMemoryByteStore, fetcher: RecordingFetcher) -> PersistentBarsCache:
    return PersistentBarsCache(store, fetcher, now=lambda: NOW)


def test_cold_request_fetches_once_and_stores_per_symbol() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    bars = cache(["AAPL", "MSFT"], TF_5M, T0, T0 + timedelta(minutes=30))
    assert fetcher.calls == [(("AAPL", "MSFT"), T0, T0 + timedelta(minutes=30))]
    assert len(bars["AAPL"]) == 6
    assert set(store.data) == {bars_key(TF_5M, "AAPL"), bars_key(TF_5M, "MSFT")}


def test_warm_request_serves_from_store_across_instances() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    window = (T0, T0 + timedelta(minutes=30))
    first = make_cache(store, fetcher)(["AAPL"], TF_5M, *window)
    second = make_cache(store, fetcher)(["AAPL"], TF_5M, *window)  # fresh instance, same store
    assert len(fetcher.calls) == 1
    assert second == first


def test_subset_request_hits_without_fetching() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=60))
    bars = cache(["AAPL"], TF_5M, T0 + timedelta(minutes=10), T0 + timedelta(minutes=30))
    assert len(fetcher.calls) == 1
    assert [b.timestamp for b in bars["AAPL"]] == [
        T0 + timedelta(minutes=m) for m in (10, 15, 20, 25)
    ]


def test_head_gap_fetches_only_missing_range() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    cache(["AAPL"], TF_5M, T0 + timedelta(minutes=30), T0 + timedelta(minutes=60))
    bars = cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=45))
    assert fetcher.calls[1] == (("AAPL",), T0, T0 + timedelta(minutes=30))
    assert len(bars["AAPL"]) == 9  # 0..40, full requested window served
    # coverage is now the union: nothing inside it re-fetches
    cache(["AAPL"], TF_5M, T0 + timedelta(minutes=5), T0 + timedelta(minutes=55))
    assert len(fetcher.calls) == 2


def test_tail_gap_fetches_only_missing_range() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=30))
    cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=60))
    assert fetcher.calls[1] == (
        ("AAPL",),
        T0 + timedelta(minutes=30),
        T0 + timedelta(minutes=60),
    )


def test_mixed_cold_and_warm_symbols_fetch_grouped_ranges() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    window = (T0, T0 + timedelta(minutes=30))
    cache(["AAPL"], TF_5M, *window)
    bars = cache(["AAPL", "MSFT"], TF_5M, *window)
    assert fetcher.calls[1] == (("MSFT",), *window)  # AAPL already covered
    assert set(bars) == {"AAPL", "MSFT"}
    assert len(bars["MSFT"]) == 6


def test_window_touching_today_stores_only_completed_days() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    end = MIDNIGHT + timedelta(hours=15)
    cache(["AAPL"], TF_5M, T0, end)
    entry = pickle.loads(store.data[bars_key(TF_5M, "AAPL")])
    assert isinstance(entry, CachedBars)
    assert entry.coverage_end == MIDNIGHT
    assert all(b.timestamp < MIDNIGHT for b in entry.bars)
    # warm call: cached part hits, only today's tail is re-fetched
    bars = cache(["AAPL"], TF_5M, T0, end)
    assert fetcher.calls[-1] == (("AAPL",), MIDNIGHT, end)
    assert bars["AAPL"][-1].timestamp < end
    assert bars["AAPL"] == sorted(bars["AAPL"], key=lambda b: b.timestamp)


def test_window_entirely_today_bypasses_store() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    window = (MIDNIGHT + timedelta(hours=14), MIDNIGHT + timedelta(hours=15))
    cache(["AAPL"], TF_5M, *window)
    assert fetcher.calls == [(("AAPL",), *window)]
    assert store.data == {}
    assert store.mget_calls == 0


def test_empty_fetch_still_widens_coverage() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    # the fetcher serves nothing before T0: an empty result must still cache
    window = (T0 - timedelta(days=1), T0 - timedelta(hours=23))
    assert cache(["AAPL"], TF_5M, *window) == {"AAPL": []}
    assert cache(["AAPL"], TF_5M, *window) == {"AAPL": []}
    assert len(fetcher.calls) == 1


def test_corrupt_payload_is_a_miss_and_overwritten() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    key = bars_key(TF_5M, "AAPL")
    store.data[key] = b"not a pickle"
    window = (T0, T0 + timedelta(minutes=30))
    bars = cache(["AAPL"], TF_5M, *window)
    assert len(fetcher.calls) == 1
    assert len(bars["AAPL"]) == 6
    assert isinstance(pickle.loads(store.data[key]), CachedBars)


def test_slice_end_is_exclusive() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=60))
    bars = cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=10))
    assert [b.timestamp for b in bars["AAPL"]] == [T0, T0 + timedelta(minutes=5)]


def test_distinct_timeframes_have_independent_entries() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    window = (T0, T0 + timedelta(minutes=30))
    cache(["AAPL"], TF_5M, *window)
    cache(["AAPL"], Timeframe.minutes(15), *window)
    assert len(fetcher.calls) == 2
    assert len(store.data) == 2
