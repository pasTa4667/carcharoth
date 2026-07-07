from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.models import Order
from alpaca.trading.requests import MarketOrderRequest

from carcharoth.domain.errors import BrokerError, OrderConflictError
from carcharoth.domain.models import OrderRequest, OrderResult, Side
from carcharoth.interfaces.execution import OrderExecutor
from carcharoth.services.alpaca.mappers import to_order_result

_SIDE_MAP = {Side.BUY: OrderSide.BUY, Side.SELL: OrderSide.SELL}

#: Alpaca rejects an order with this code when an opposite-side order is open.
_WASH_TRADE_CODE = 40310000


class AlpacaOrderExecutor(OrderExecutor):
    def __init__(self, client: TradingClient) -> None:
        self._client = client

    def submit(self, request: OrderRequest) -> OrderResult:
        sdk_request = MarketOrderRequest(
            symbol=request.symbol,
            qty=request.qty,
            side=_SIDE_MAP[request.side],
            time_in_force=TimeInForce.DAY,
            client_order_id=request.client_order_id,
        )
        try:
            order = self._client.submit_order(sdk_request)
        except APIError as exc:
            if exc.code == _WASH_TRADE_CODE:
                raise OrderConflictError(
                    f"wash-trade rejection for {request.symbol}: {exc}"
                ) from exc
            raise BrokerError(f"order submission failed: {exc}", retryable=False) from exc
        except Exception as exc:
            raise BrokerError(f"order submission failed: {exc}", retryable=False) from exc
        assert isinstance(order, Order)
        return to_order_result(order)

    def get_order(self, broker_order_id: str) -> OrderResult:
        try:
            order = self._client.get_order_by_id(broker_order_id)
        except Exception as exc:
            raise BrokerError(f"order lookup failed: {exc}", retryable=True) from exc
        assert isinstance(order, Order)
        return to_order_result(order)

    def cancel_order(self, broker_order_id: str) -> None:
        try:
            self._client.cancel_order_by_id(broker_order_id)
        except Exception as exc:
            raise BrokerError(f"order cancellation failed: {exc}", retryable=True) from exc
