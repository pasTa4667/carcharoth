from typing import Any
from uuid import uuid4

import pytest
from alpaca.trading.enums import AssetClass, AssetExchange, AssetStatus
from alpaca.trading.models import Asset

from carcharoth.domain.errors import BrokerError, ShortUnavailableError
from carcharoth.domain.models import OrderRequest, TradeIntent
from carcharoth.services.alpaca.execution import AlpacaOrderExecutor
from tests.test_mappers import make_alpaca_order


def asset(*, tradable: bool = True, shortable: bool = True) -> Asset:
    return Asset(
        **{
            "id": uuid4(),
            "class": AssetClass.US_EQUITY,
            "exchange": AssetExchange.NASDAQ,
            "symbol": "AAPL",
            "name": "Apple",
            "status": AssetStatus.ACTIVE,
            "tradable": tradable,
            "marginable": True,
            "shortable": shortable,
            "easy_to_borrow": True,
            "fractionable": True,
        }
    )


class FakeTradingClient:
    def __init__(self, record: Asset | Exception) -> None:
        self.record = record
        self.asset_calls: list[str] = []
        self.submitted: list[Any] = []

    def get_asset(self, symbol: str) -> Asset:
        self.asset_calls.append(symbol)
        if isinstance(self.record, Exception):
            raise self.record
        return self.record

    def submit_order(self, request: Any) -> Any:
        self.submitted.append(request)
        return make_alpaca_order(side=request.side, qty=str(request.qty))


def executor(client: FakeTradingClient) -> AlpacaOrderExecutor:
    return AlpacaOrderExecutor(client)  # type: ignore[arg-type]


def test_open_short_preflights_shortability() -> None:
    client = FakeTradingClient(asset())
    result = executor(client).submit(OrderRequest("AAPL", TradeIntent.OPEN_SHORT, 5))
    assert client.asset_calls == ["AAPL"]
    assert len(client.submitted) == 1
    assert result.qty == 5


@pytest.mark.parametrize("record", [asset(shortable=False), asset(tradable=False)])
def test_unavailable_short_is_rejected_before_submission(record: Asset) -> None:
    client = FakeTradingClient(record)
    with pytest.raises(ShortUnavailableError):
        executor(client).submit(OrderRequest("AAPL", TradeIntent.OPEN_SHORT, 5))
    assert client.submitted == []


def test_long_order_does_not_fetch_asset() -> None:
    client = FakeTradingClient(asset(shortable=False))
    executor(client).submit(OrderRequest("AAPL", TradeIntent.OPEN_LONG, 5))
    assert client.asset_calls == []


def test_shortability_lookup_failure_is_retryable_broker_error() -> None:
    client = FakeTradingClient(ConnectionError("down"))
    with pytest.raises(BrokerError) as caught:
        executor(client).submit(OrderRequest("AAPL", TradeIntent.OPEN_SHORT, 5))
    assert caught.value.retryable
