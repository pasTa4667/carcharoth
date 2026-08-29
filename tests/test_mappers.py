from datetime import UTC, datetime
from uuid import uuid4

from alpaca.trading.enums import (
    AssetClass,
    AssetExchange,
    OrderClass,
    OrderSide,
    OrderType,
    PositionSide,
)
from alpaca.trading.enums import (
    OrderStatus as AlpacaOrderStatus,
)
from alpaca.trading.enums import (
    TimeInForce as AlpacaTimeInForce,
)
from alpaca.trading.models import Order as AlpacaOrder
from alpaca.trading.models import Position as AlpacaPosition

from carcharoth.domain.models import OrderStatus, PositionDirection, Side
from carcharoth.services.alpaca.mappers import to_order_result, to_order_status, to_position

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


def test_short_position_side_is_normalized_to_signed_domain_values() -> None:
    position = AlpacaPosition(
        asset_id=uuid4(),
        symbol="AAPL",
        exchange=AssetExchange.NASDAQ,
        asset_class=AssetClass.US_EQUITY,
        asset_marginable=True,
        avg_entry_price="100",
        qty="5",
        side=PositionSide.SHORT,
        market_value="500",  # normalize even if provider sends an absolute value
        cost_basis="500",
        unrealized_pl="10",
        unrealized_plpc="0.02",
        unrealized_intraday_pl="0",
        unrealized_intraday_plpc="0",
        current_price="98",
        lastday_price="99",
        change_today="0",
    )
    mapped = to_position(position)
    assert mapped.qty == -5
    assert mapped.market_value == -500
    assert mapped.direction is PositionDirection.SHORT
    assert mapped.unrealized_pnl == 10


def test_terminal_statuses_map_directly() -> None:
    assert to_order_status(AlpacaOrderStatus.FILLED) is OrderStatus.FILLED
    assert to_order_status(AlpacaOrderStatus.CANCELED) is OrderStatus.CANCELED
    assert to_order_status(AlpacaOrderStatus.REJECTED) is OrderStatus.REJECTED
    assert to_order_status(AlpacaOrderStatus.EXPIRED) is OrderStatus.EXPIRED
