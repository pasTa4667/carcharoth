from datetime import UTC, datetime
from uuid import uuid4

from alpaca.trading.enums import (
    AssetClass,
    OrderClass,
    OrderSide,
    OrderType,
)
from alpaca.trading.enums import (
    OrderStatus as AlpacaOrderStatus,
)
from alpaca.trading.enums import (
    TimeInForce as AlpacaTimeInForce,
)
from alpaca.trading.models import Order as AlpacaOrder

from carcharoth.domain.models import OrderStatus, Side
from carcharoth.services.alpaca.mappers import to_order_result, to_order_status

NOW = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)


def make_alpaca_order(**overrides: object) -> AlpacaOrder:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "client_order_id": "abc123",
        "created_at": NOW,
        "updated_at": NOW,
        "submitted_at": NOW,
        "filled_at": None,
        "expired_at": None,
        "canceled_at": None,
        "failed_at": None,
        "replaced_at": None,
        "replaced_by": None,
        "replaces": None,
        "asset_id": uuid4(),
        "symbol": "AAPL",
        "asset_class": AssetClass.US_EQUITY,
        "notional": None,
        "qty": "10",
        "filled_qty": "0",
        "filled_avg_price": None,
        "order_class": OrderClass.SIMPLE,
        "order_type": OrderType.MARKET,
        "type": OrderType.MARKET,
        "side": OrderSide.BUY,
        "time_in_force": AlpacaTimeInForce.DAY,
        "limit_price": None,
        "stop_price": None,
        "status": AlpacaOrderStatus.ACCEPTED,
        "extended_hours": False,
        "legs": None,
        "trail_percent": None,
        "trail_price": None,
        "hwm": None,
    }
    defaults.update(overrides)
    return AlpacaOrder(**defaults)  # type: ignore[arg-type]


def test_accepted_order_maps() -> None:
    order = make_alpaca_order()
    result = to_order_result(order)
    assert result.broker_order_id == str(order.id)
    assert result.side is Side.BUY
    assert result.qty == 10
    assert result.status is OrderStatus.ACCEPTED
    assert result.filled_avg_price is None
    assert result.filled_at is None


def test_filled_order_maps() -> None:
    order = make_alpaca_order(
        status=AlpacaOrderStatus.FILLED,
        filled_qty="10",
        filled_avg_price="150.25",
        filled_at=NOW,
        side=OrderSide.SELL,
    )
    result = to_order_result(order)
    assert result.status is OrderStatus.FILLED
    assert result.side is Side.SELL
    assert result.filled_qty == 10
    assert result.filled_avg_price == 150.25
    assert result.filled_at == NOW


def test_unknown_status_maps_to_non_terminal() -> None:
    assert to_order_status(AlpacaOrderStatus.PENDING_REVIEW) is OrderStatus.ACCEPTED
    assert to_order_status(AlpacaOrderStatus.DONE_FOR_DAY) is OrderStatus.ACCEPTED


def test_terminal_statuses_map_directly() -> None:
    assert to_order_status(AlpacaOrderStatus.FILLED) is OrderStatus.FILLED
    assert to_order_status(AlpacaOrderStatus.CANCELED) is OrderStatus.CANCELED
    assert to_order_status(AlpacaOrderStatus.REJECTED) is OrderStatus.REJECTED
    assert to_order_status(AlpacaOrderStatus.EXPIRED) is OrderStatus.EXPIRED
