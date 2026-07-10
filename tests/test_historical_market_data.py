"""HistoricalMarketDataService: cursor windowing, quotes and the tick grid."""

from datetime import timedelta

import pytest

from carcharoth.domain.errors import MarketDataError
from carcharoth.domain.models import BarSpec, Timeframe
from carcharoth.services.backtest.market_data import HistoricalMarketDataService
from tests.factories import BASE_TIME, make_bars

SPREAD = 0.001
SPEC = BarSpec(Timeframe.minutes(5), lookback=3)


def make_service(prices: list[float] | None = None) -> HistoricalMarketDataService:
    bars = make_bars(prices or [100, 101, 102, 103, 104])  # 5-min bars from BASE_TIME
    return HistoricalMarketDataService({"AAPL": bars}, spread_pct=SPREAD)


def test_snapshot_returns_trailing_lookback_bars_up_to_cursor() -> None:
    service = make_service()
    cursor = BASE_TIME + timedelta(minutes=15)  # bar index 3 (close 103)
    service.advance_to(cursor)

    snapshot = service.get_snapshot(["AAPL"], SPEC)

    closes = [bar.close for bar in snapshot.bars["AAPL"]]
    assert closes == [101, 102, 103]  # exactly lookback bars, newest <= cursor
    assert snapshot.as_of == cursor


def test_snapshot_quote_derived_from_newest_bar_close() -> None:
    service = make_service()
    cursor = BASE_TIME + timedelta(minutes=10)  # close 102
    service.advance_to(cursor)

    quote = service.get_snapshot(["AAPL"], SPEC).quotes["AAPL"]

    assert quote.bid_price == pytest.approx(102 * (1 - SPREAD / 2))
    assert quote.ask_price == pytest.approx(102 * (1 + SPREAD / 2))
    assert quote.timestamp == cursor


def test_snapshot_before_first_bar_is_empty_with_no_quote() -> None:
    service = make_service()
    service.advance_to(BASE_TIME - timedelta(minutes=1))

    snapshot = service.get_snapshot(["AAPL"], SPEC)

    assert snapshot.bars["AAPL"] == []
    assert "AAPL" not in snapshot.quotes


def test_snapshot_for_unknown_symbol_is_empty() -> None:
    service = make_service()
    service.advance_to(BASE_TIME)
    snapshot = service.get_snapshot(["MSFT"], SPEC)
    assert snapshot.bars["MSFT"] == []
    assert "MSFT" not in snapshot.quotes


def test_snapshot_without_cursor_raises() -> None:
    with pytest.raises(MarketDataError, match="cursor not set"):
        make_service().get_snapshot(["AAPL"], SPEC)


def test_timestamp_grid_is_sorted_union_within_range() -> None:
    aapl = make_bars([100, 101, 102])
    msft = make_bars([200, 201], start=BASE_TIME + timedelta(minutes=2))
    service = HistoricalMarketDataService({"AAPL": aapl, "MSFT": msft}, spread_pct=SPREAD)

    grid = service.timestamp_grid(BASE_TIME, BASE_TIME + timedelta(minutes=7))

    assert grid == [
        BASE_TIME,
        BASE_TIME + timedelta(minutes=2),
        BASE_TIME + timedelta(minutes=5),
        BASE_TIME + timedelta(minutes=7),
    ]


def test_latest_closes_per_symbol_at_cursor() -> None:
    aapl = make_bars([100, 101, 102])
    msft = make_bars([200, 201], start=BASE_TIME + timedelta(minutes=20))  # starts later
    service = HistoricalMarketDataService({"AAPL": aapl, "MSFT": msft}, spread_pct=SPREAD)

    service.advance_to(BASE_TIME + timedelta(minutes=5))
    assert service.latest_closes() == {"AAPL": 101}  # MSFT has no bar yet

    service.advance_to(BASE_TIME + timedelta(minutes=25))
    assert service.latest_closes() == {"AAPL": 102, "MSFT": 201}
