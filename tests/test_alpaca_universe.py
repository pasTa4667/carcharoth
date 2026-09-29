from datetime import UTC, date, datetime
from types import SimpleNamespace

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.trading.enums import AssetClass, AssetStatus

from carcharoth.domain.models import Timeframe
from carcharoth.services.alpaca.historical import fetch_historical_bars
from carcharoth.services.alpaca.universe import fetch_assets, fetch_calendar


def asset(symbol: str, *, exchange: str = "NYSE", tradable: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"id-{symbol}",
        symbol=symbol,
        name=f"{symbol} fund",
        exchange=SimpleNamespace(value=exchange),
        status=AssetStatus.ACTIVE,
        asset_class=AssetClass.US_EQUITY,
        tradable=tradable,
    )


def test_alpaca_universe_filters_and_keeps_funds() -> None:
    client = SimpleNamespace(
        get_all_assets=lambda request: [
            asset("SPY"),
            asset("OTC", exchange="OTC"),
            asset("NO", tradable=False),
        ]
    )
    assert [record.symbol for record in fetch_assets(client, set())] == ["SPY"]


def test_calendar_and_explicit_bar_semantics() -> None:
    client = SimpleNamespace(
        get_calendar=lambda request: [
            SimpleNamespace(
                date=date(2020, 1, 2),
                open=datetime(2020, 1, 2, 9, 30),
                close=datetime(2020, 1, 2, 16),
            )
        ]
    )
    assert fetch_calendar(client, date(2020, 1, 1), date(2020, 1, 3)) == [date(2020, 1, 2)]

    seen = {}

    class DataClient:
        def get_stock_bars(self, request):
            seen["request"] = request
            return SimpleNamespace(data={})

    result = fetch_historical_bars(
        DataClient(),
        ["SPY"],
        Timeframe.daily(),
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2020, 2, 1, tzinfo=UTC),
        feed="sip",
        adjustment="split",
        asof="-",
    )
    assert result == {"SPY": []}
    assert seen["request"].feed is DataFeed.SIP
    assert seen["request"].adjustment is Adjustment.SPLIT
    assert seen["request"].asof == "-"
