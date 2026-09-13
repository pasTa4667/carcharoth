"""Persistent bars cache: gap-fill, grouping, today-clamp, corrupt payloads."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from carcharoth.domain.models import Bar, Timeframe
from carcharoth.services.cache.bars import PersistentBarsCache
from carcharoth.services.cache.bars import bars_key as monthly_key
from carcharoth.services.cache.bars_codec import decode
from tests.fakes import InMemoryByteStore


def bars_key(timeframe: Timeframe, symbol: str) -> str:
    return monthly_key(timeframe, symbol, "2026-06")


def read_chunk(payload: bytes):
    return decode(
        payload,
        "AAPL",
        "5minute",
        "2026-06",
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 7, 1, tzinfo=UTC),
    )


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
    entry = read_chunk(store.data[bars_key(TF_5M, "AAPL")])
    assert entry.coverage[-1][1] == MIDNIGHT
    assert all(b.timestamp < MIDNIGHT for b in entry.bars("AAPL", T0, end))
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
    assert read_chunk(store.data[key]).coverage == (window,)


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


def test_disjoint_requests_do_not_invent_coverage() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=10))
    cache(["AAPL"], TF_5M, T0 + timedelta(minutes=30), T0 + timedelta(minutes=40))
    assert len(read_chunk(store.data[bars_key(TF_5M, "AAPL")]).coverage) == 2
    cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=40))
    assert fetcher.calls[-1][1:] == (T0 + timedelta(minutes=10), T0 + timedelta(minutes=30))


def test_month_subset_reads_only_overlapping_key_and_v1_is_untouched() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    store.data["carch:bars:v1:5minute:AAPL"] = b"do not unpickle"
    cache = make_cache(store, fetcher)
    cache(["AAPL"], TF_5M, T0 - timedelta(days=2), T0 + timedelta(minutes=10))
    store.read_keys.clear()
    cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=10))
    assert store.read_keys == [bars_key(TF_5M, "AAPL")]
    assert store.data["carch:bars:v1:5minute:AAPL"] == b"do not unpickle"


def test_input_validation_and_empty_requests() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    cache = make_cache(store, fetcher)
    assert cache([], TF_5M, T0, NOW) == {}
    assert cache(["AAPL"], TF_5M, T0, T0) == {"AAPL": []}
    with pytest.raises(ValueError, match="precedes"):
        cache(["AAPL"], TF_5M, NOW, T0)
    with pytest.raises(ValueError, match="aware"):
        cache(["AAPL"], TF_5M, T0.replace(tzinfo=None), NOW)
    assert not fetcher.calls and not store.mget_calls


def test_upstream_clipping_duplicates_and_fractional_volume_roundtrip() -> None:
    from dataclasses import replace

    rows = [
        bar("AAPL", 10),
        bar("AAPL", 0),
        bar("AAPL", -5),
        replace(bar("AAPL", 0), volume=0.125, close=-2.5),
    ]
    store = InMemoryByteStore()
    cache = PersistentBarsCache(store, lambda *args: {"AAPL": rows}, now=lambda: NOW)
    expected = {"AAPL": [rows[-1]]}
    assert cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=10)) == expected
    assert cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=10)) == expected


def test_failed_and_noop_writes_return_current_call_rows() -> None:
    from carcharoth.services.cache.resilient import ResilientByteStore

    class PartialStore(InMemoryByteStore):
        def mset(self, items):
            key = next(iter(items))
            self.data[key] = items[key]
            raise ConnectionError("partial write")

    store, fetcher = PartialStore(), RecordingFetcher()
    cache = PersistentBarsCache(ResilientByteStore(store), fetcher, now=lambda: NOW)
    window = (T0, T0 + timedelta(minutes=10))
    first = cache(["AAPL", "MSFT"], TF_5M, *window)
    second = make_cache(store, fetcher)(["AAPL"], TF_5M, *window)
    assert first["AAPL"] == second["AAPL"]
    assert len(first["MSFT"]) == 2


def test_stale_writer_loses_coverage_but_never_invents_it() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    other = make_cache(store, fetcher)
    nested = False

    def fetch(symbols, timeframe, start, end):
        nonlocal nested
        if not nested:
            nested = True
            other(symbols, timeframe, T0 + timedelta(minutes=20), T0 + timedelta(minutes=30))
        return fetcher(symbols, timeframe, start, end)

    cache = PersistentBarsCache(store, fetch, now=lambda: NOW)
    cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=10))
    assert read_chunk(store.data[bars_key(TF_5M, "AAPL")]).coverage == (
        (T0, T0 + timedelta(minutes=10)),
    )
    other(["AAPL"], TF_5M, T0 + timedelta(minutes=20), T0 + timedelta(minutes=30))
    assert fetcher.calls[-1][1:] == (T0 + timedelta(minutes=20), T0 + timedelta(minutes=30))
    assert len(store.data) == 1


def test_upstream_failure_does_not_record_empty_coverage() -> None:
    def fail(*args):
        raise RuntimeError("upstream failed")

    store = InMemoryByteStore()
    cache = PersistentBarsCache(store, fail, now=lambda: NOW)
    with pytest.raises(RuntimeError, match="upstream"):
        cache(["AAPL"], TF_5M, T0, NOW)
    assert store.data == {}


@pytest.mark.parametrize(
    "corruption", ["identity", "duplicate", "coverage", "month", "null", "timezone"]
)
def test_codec_rejects_invalid_self_contained_values(corruption) -> None:
    import json

    import pyarrow as pa

    from carcharoth.services.cache.bars_codec import Chunk, encode

    chunk = Chunk.empty().merge([bar("AAPL", 0)], (T0, T0 + timedelta(minutes=5)))
    payload = encode(chunk, "AAPL", "5minute", "2026-06")
    table = pa.ipc.open_file(pa.BufferReader(payload)).read_all()
    metadata = json.loads(table.schema.metadata[b"carcharoth"])
    if corruption == "identity":
        metadata["symbol"] = "OTHER"
    elif corruption == "coverage":
        metadata["coverage"] = []
    elif corruption == "month":
        metadata["coverage"] = [[T0.isoformat(), NOW.replace(month=7).isoformat()]]
    elif corruption == "duplicate":
        table = pa.concat_tables([table, table])
    elif corruption == "null":
        table = table.set_column(1, table.schema.field(1), pa.array([None], type=pa.float64()))
    else:
        table = table.set_column(
            0, "timestamp", pa.array([T0.replace(tzinfo=None)], type=pa.timestamp("us"))
        )
    table = table.replace_schema_metadata({b"carcharoth": json.dumps(metadata).encode()})
    sink = pa.BufferOutputStream()
    with pa.ipc.new_file(sink, table.schema) as writer:
        writer.write_table(table)
    with pytest.raises(ValueError):
        read_chunk(bytes(sink.getvalue()))


def test_preexisting_future_coverage_never_serves_today_and_rollover_refetches() -> None:
    store, fetcher = InMemoryByteStore(), RecordingFetcher()
    future_now = NOW + timedelta(days=2)
    end = MIDNIGHT + timedelta(days=1)
    PersistentBarsCache(store, fetcher, now=lambda: future_now)(["AAPL"], TF_5M, T0, end)
    cache = make_cache(store, fetcher)
    cache(["AAPL"], TF_5M, T0, end)
    assert fetcher.calls[-1][1:] == (MIDNIGHT, end)
    cache(["AAPL"], TF_5M, T0, end)
    assert fetcher.calls[-1][1:] == (MIDNIGHT, end)
    # A fresh store caches yesterday's formerly fresh tail on the next UTC day.
    store = InMemoryByteStore()
    make_cache(store, fetcher)(["AAPL"], TF_5M, T0, end)
    PersistentBarsCache(store, fetcher, now=lambda: NOW + timedelta(days=1))(
        ["AAPL"], TF_5M, T0, end
    )
    assert fetcher.calls[-1][1:] == (MIDNIGHT, end)


@pytest.mark.parametrize(
    "start,end",
    [
        (datetime(2024, 2, 28, tzinfo=UTC), datetime(2024, 3, 2, tzinfo=UTC)),
        (datetime(2024, 12, 31, tzinfo=UTC), datetime(2025, 1, 2, tzinfo=UTC)),
    ],
)
def test_calendar_boundaries_empty_months_and_microsecond_precision(start, end) -> None:
    from dataclasses import replace

    row = replace(bar("AAPL", 0), timestamp=start + timedelta(microseconds=123456), volume=1.25)
    calls = []

    def fetch(symbols, timeframe, a, b):
        calls.append((a, b))
        return {"AAPL": [row]}  # deliberately out-of-range on later requests

    store = InMemoryByteStore()
    cache = PersistentBarsCache(store, fetch, now=lambda: NOW)
    assert cache(["AAPL"], TF_5M, start, end) == {"AAPL": [row]}
    assert cache(["AAPL"], TF_5M, start, end) == {"AAPL": [row]}
    assert calls == [(start, end)]
    assert len(store.data) == 2
