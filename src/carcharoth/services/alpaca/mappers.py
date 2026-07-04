"""Pure conversions from alpaca-py SDK models to domain models.

This module (plus the service classes in this package) is the only place
alpaca-py types appear; nothing outside services/alpaca/ imports the SDK.
"""

from alpaca.data.models import Bar as AlpacaBar
from alpaca.data.models import Quote as AlpacaQuote
from alpaca.trading.enums import OrderStatus as AlpacaOrderStatus
from alpaca.trading.models import Order as AlpacaOrder
from alpaca.trading.models import Position as AlpacaPosition
from alpaca.trading.models import TradeAccount

from carcharoth.domain.models import (
    AccountState,
    Bar,
    OrderResult,
    OrderStatus,
    Position,
    Quote,
    Side,
)

_ORDER_STATUS_MAP: dict[AlpacaOrderStatus, OrderStatus] = {
    AlpacaOrderStatus.NEW: OrderStatus.NEW,
    AlpacaOrderStatus.ACCEPTED: OrderStatus.ACCEPTED,
    AlpacaOrderStatus.PENDING_NEW: OrderStatus.NEW,
    AlpacaOrderStatus.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
    AlpacaOrderStatus.FILLED: OrderStatus.FILLED,
    AlpacaOrderStatus.CANCELED: OrderStatus.CANCELED,
    AlpacaOrderStatus.EXPIRED: OrderStatus.EXPIRED,
    AlpacaOrderStatus.REJECTED: OrderStatus.REJECTED,
}


def to_order_status(status: AlpacaOrderStatus) -> OrderStatus:
    # Unknown/exotic statuses map to ACCEPTED (non-terminal) so the engine
    # keeps reconciling the order instead of wrongly declaring it done.
    return _ORDER_STATUS_MAP.get(status, OrderStatus.ACCEPTED)


def to_bar(bar: AlpacaBar) -> Bar:
    return Bar(
        symbol=bar.symbol,
        timestamp=bar.timestamp,
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=float(bar.volume),
    )


def to_quote(quote: AlpacaQuote) -> Quote:
    return Quote(
        symbol=quote.symbol,
        timestamp=quote.timestamp,
        bid_price=float(quote.bid_price),
        ask_price=float(quote.ask_price),
        bid_size=float(quote.bid_size),
        ask_size=float(quote.ask_size),
    )


def to_position(position: AlpacaPosition) -> Position:
    return Position(
        symbol=position.symbol,
        qty=float(position.qty),
        avg_entry_price=float(position.avg_entry_price),
        market_value=float(position.market_value or 0),
        unrealized_pnl=float(position.unrealized_pl or 0),
        current_price=float(position.current_price or 0),
    )


def to_account_state(account: TradeAccount, positions: list[AlpacaPosition]) -> AccountState:
    return AccountState(
        equity=float(account.equity or 0),
        cash=float(account.cash or 0),
        buying_power=float(account.buying_power or 0),
        last_equity=float(account.last_equity or 0),
        positions={p.symbol: to_position(p) for p in positions},
        currency=account.currency or "USD",
    )


def to_order_result(order: AlpacaOrder) -> OrderResult:
    if order.symbol is None or order.side is None:
        raise ValueError(f"broker order {order.id} is missing symbol or side")
    return OrderResult(
        broker_order_id=str(order.id),
        client_order_id=order.client_order_id,
        symbol=order.symbol,
        side=Side(order.side.value),
        qty=float(order.qty or 0),
        status=to_order_status(order.status),
        filled_qty=float(order.filled_qty or 0),
        filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price else None,
        submitted_at=order.submitted_at or order.created_at,
        filled_at=order.filled_at,
    )
