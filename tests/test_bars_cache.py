"""Union-window bars cache: cache hits, widening, slice boundaries."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from carcharoth.domain.models import Bar, Timeframe
from carcharoth.optimize.bars_cache import BarsCache

T0 = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
TF_5M = Timeframe.minutes(5)


def bar(symbol: str, minute: int) -> Bar:
    ts = T0 + timedelta(minutes=minute)
    return Bar(symbol=symbol, timestamp=ts, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)


class CountingFetcher:
    """Serves 5-minute bars for the requested window and counts fetches."""

    def __init__(self) -> None:
        self.calls: list[tuple[datetime, datetime]] = []

    def __call__(
        self, symbols: Sequence[str], timeframe: Timeframe, start: datetime, end: datetime
    ) -> dict[str, list[Bar]]:
        self.calls.append((start, end))
        minutes = range(0, 24 * 60, 5)
        return {
            s: [bar(s, m) for m in minutes if start <= T0 + timedelta(minutes=m) < end]
            for s in symbols
        }


def test_first_request_fetches() -> None:
    fetcher = CountingFetcher()
    cache = BarsCache(fetcher)
    bars = cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=30))
    assert len(fetcher.calls) == 1
    assert len(bars["AAPL"]) == 6


def test_subset_request_hits_cache() -> None:
    fetcher = CountingFetcher()
    cache = BarsCache(fetcher)
    cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=60))
    bars = cache(["AAPL"], TF_5M, T0 + timedelta(minutes=10), T0 + timedelta(minutes=30))
    assert len(fetcher.calls) == 1  # no second fetch
    assert [b.timestamp for b in bars["AAPL"]] == [
        T0 + timedelta(minutes=m) for m in (10, 15, 20, 25)
    ]


def test_wider_request_fetches_union_window_once() -> None:
    fetcher = CountingFetcher()
    cache = BarsCache(fetcher)
    cache(["AAPL"], TF_5M, T0 + timedelta(minutes=30), T0 + timedelta(minutes=60))
    # larger warm-up prefix -> start moves earlier, end stays inside
    cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=45))
    assert fetcher.calls == [
        (T0 + timedelta(minutes=30), T0 + timedelta(minutes=60)),
        (T0, T0 + timedelta(minutes=60)),  # union of both windows
    ]
    # now fully covered: no further fetch for anything inside the union
    cache(["AAPL"], TF_5M, T0 + timedelta(minutes=5), T0 + timedelta(minutes=55))
    assert len(fetcher.calls) == 2


def test_slice_end_is_exclusive() -> None:
    fetcher = CountingFetcher()
    cache = BarsCache(fetcher)
    cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=60))
    bars = cache(["AAPL"], TF_5M, T0, T0 + timedelta(minutes=10))
    assert [b.timestamp for b in bars["AAPL"]] == [T0, T0 + timedelta(minutes=5)]


def test_distinct_timeframes_have_independent_entries() -> None:
    fetcher = CountingFetcher()
    cache = BarsCache(fetcher)
    window = (T0, T0 + timedelta(minutes=30))
    cache(["AAPL"], TF_5M, *window)
    cache(["AAPL"], Timeframe.minutes(15), *window)
    assert len(fetcher.calls) == 2


def test_symbol_order_does_not_matter() -> None:
    fetcher = CountingFetcher()
    cache = BarsCache(fetcher)
    window = (T0, T0 + timedelta(minutes=30))
    cache(["MSFT", "AAPL"], TF_5M, *window)
    bars = cache(["AAPL", "MSFT"], TF_5M, *window)
    assert len(fetcher.calls) == 1
    assert set(bars) == {"MSFT", "AAPL"}
