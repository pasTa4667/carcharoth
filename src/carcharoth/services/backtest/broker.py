"""In-memory broker with signed long/short holdings and instant fills."""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from uuid import uuid4

from carcharoth.domain.errors import BrokerError
from carcharoth.domain.models import (
    AccountState,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Position,
    TradeIntent,
)
from carcharoth.interfaces.account import AccountService
from carcharoth.interfaces.execution import OrderExecutor
from carcharoth.strategies.session import MARKET_TZ

logger = logging.getLogger(__name__)
_SECONDS_PER_YEAR = 365.0 * 24 * 60 * 60
_EPSILON = 1e-9


@dataclass
class _Holding:
    qty: float  # signed: positive long, negative short
    avg_entry_price: float


class SimulatedBroker(AccountService, OrderExecutor):
    def __init__(
        self,
        initial_capital: float,
        spread_pct: float,
        slippage_pct: float,
        margin_leverage: float = 1.0,
        short_borrow_apr: float = 0.0,
    ) -> None:
        self._cash = initial_capital
        self._spread_pct = spread_pct
        self._slippage_pct = slippage_pct
        self._margin_leverage = margin_leverage
        self._short_borrow_apr = short_borrow_apr
        self._holdings: dict[str, _Holding] = {}
        self._closes: dict[str, float] = {}
        self._as_of: datetime | None = None
        self._last_equity = initial_capital
        self._session_date: date | None = None
        self._fills: dict[str, OrderResult] = {}
        self._short_fees: dict[str, float] = {}

    def mark_to_market(self, as_of: datetime, closes: dict[str, float]) -> None:
        """Set simulated time/prices and accrue borrow against elapsed time."""
        if self._as_of is not None:
            self._accrue_short_fees(as_of)
        session_date = as_of.astimezone(MARKET_TZ).date()
        if self._session_date is not None and session_date != self._session_date:
            self._last_equity = self._equity()
        self._session_date = session_date
        self._as_of = as_of
        self._closes.update(closes)

    def get_account_state(self) -> AccountState:
        positions = {symbol: self._position(symbol) for symbol in self._holdings}
        return AccountState(
            equity=self._equity(),
            cash=self._cash,
            buying_power=self._buying_power(),
            last_equity=self._last_equity,
            positions=positions,
        )

    def submit(self, request: OrderRequest) -> OrderResult:
        as_of = self._require_time()
        close = self._closes.get(request.symbol)
        if close is None:
            raise BrokerError(f"no price for {request.symbol} at {as_of}")
        if request.qty <= 0:
            raise BrokerError("order quantity must be positive")

        if request.intent is TradeIntent.OPEN_LONG:
            fill_price, fees = self._open_long(request, close)
        elif request.intent is TradeIntent.CLOSE_LONG:
            fill_price, fees = self._close_long(request, close)
        elif request.intent is TradeIntent.OPEN_SHORT:
            fill_price, fees = self._open_short(request, close)
        elif request.intent is TradeIntent.CLOSE_SHORT:
            fill_price, fees = self._close_short(request, close)
        else:
            raise BrokerError("cannot submit a HOLD intent")

        broker_order_id = uuid4().hex
        self._fills[broker_order_id] = OrderResult(
            broker_order_id=broker_order_id,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            status=OrderStatus.FILLED,
            filled_qty=request.qty,
            filled_avg_price=fill_price,
            submitted_at=as_of,
            filled_at=as_of,
            fees=fees,
        )
        return OrderResult(
            broker_order_id=broker_order_id,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            status=OrderStatus.ACCEPTED,
            filled_qty=0,
            filled_avg_price=None,
            submitted_at=as_of,
            filled_at=None,
        )

    def get_order(self, broker_order_id: str) -> OrderResult:
        fill = self._fills.get(broker_order_id)
        if fill is None:
            raise BrokerError(f"unknown order {broker_order_id}")
        return fill

    def cancel_order(self, broker_order_id: str) -> None:
        logger.debug("cancel_order(%s) ignored: simulated fills are instant", broker_order_id)

    def _open_long(self, request: OrderRequest, close: float) -> tuple[float, float]:
        self._require_flat(request.symbol)
        fill_price = self._buy_price(close)
        self._require_buying_power(request.qty * fill_price)
        self._cash -= fill_price * request.qty
        self._holdings[request.symbol] = _Holding(request.qty, fill_price)
        return fill_price, 0.0

    def _close_long(self, request: OrderRequest, close: float) -> tuple[float, float]:
        holding = self._require_direction(request.symbol, long=True)
        if request.qty > holding.qty + _EPSILON:
            raise BrokerError(f"cannot close {request.qty} {request.symbol}, long {holding.qty}")
        fill_price = self._sell_price(close)
        self._cash += fill_price * request.qty
        holding.qty -= request.qty
        self._drop_if_flat(request.symbol, holding)
        return fill_price, 0.0

    def _open_short(self, request: OrderRequest, close: float) -> tuple[float, float]:
        self._require_flat(request.symbol)
        fill_price = self._sell_price(close)
        self._require_buying_power(request.qty * fill_price)
        self._cash += fill_price * request.qty
        self._holdings[request.symbol] = _Holding(-request.qty, fill_price)
        self._short_fees[request.symbol] = 0.0
        return fill_price, 0.0

    def _close_short(self, request: OrderRequest, close: float) -> tuple[float, float]:
        holding = self._require_direction(request.symbol, long=False)
        short_qty = abs(holding.qty)
        if request.qty > short_qty + _EPSILON:
            raise BrokerError(f"cannot cover {request.qty} {request.symbol}, short {short_qty}")
        fill_price = self._buy_price(close)
        self._cash -= fill_price * request.qty
        fee_pool = self._short_fees.get(request.symbol, 0.0)
        fees = fee_pool * request.qty / short_qty
        self._short_fees[request.symbol] = max(0.0, fee_pool - fees)
        holding.qty += request.qty
        self._drop_if_flat(request.symbol, holding)
        return fill_price, fees

    def _require_flat(self, symbol: str) -> None:
        if symbol in self._holdings:
            raise BrokerError(f"position already open for {symbol}; no pyramiding or reversal")

    def _require_direction(self, symbol: str, *, long: bool) -> _Holding:
        holding = self._holdings.get(symbol)
        expected = "long" if long else "short"
        if holding is None or (holding.qty > 0) is not long:
            actual = "flat" if holding is None else ("long" if holding.qty > 0 else "short")
            raise BrokerError(f"cannot close {expected} {symbol}; position is {actual}")
        return holding

    def _drop_if_flat(self, symbol: str, holding: _Holding) -> None:
        if abs(holding.qty) <= _EPSILON:
            del self._holdings[symbol]
            self._short_fees.pop(symbol, None)

    def _require_buying_power(self, notional: float) -> None:
        available = self._buying_power()
        if notional > available + _EPSILON:
            raise BrokerError(
                f"insufficient buying power: need {notional:.2f}, have {available:.2f}"
            )

    def _buy_price(self, close: float) -> float:
        return close * (1 + self._spread_pct / 2) * (1 + self._slippage_pct)

    def _sell_price(self, close: float) -> float:
        return close * (1 - self._spread_pct / 2) * (1 - self._slippage_pct)

    def _position(self, symbol: str) -> Position:
        holding = self._holdings[symbol]
        price = self._closes.get(symbol, holding.avg_entry_price)
        return Position(
            symbol=symbol,
            qty=holding.qty,
            avg_entry_price=holding.avg_entry_price,
            market_value=holding.qty * price,
            unrealized_pnl=(price - holding.avg_entry_price) * holding.qty,
            current_price=price,
        )

    def _equity(self) -> float:
        return self._cash + sum(
            holding.qty * self._closes.get(symbol, holding.avg_entry_price)
            for symbol, holding in self._holdings.items()
        )

    def _gross_exposure(self) -> float:
        return sum(
            abs(holding.qty * self._closes.get(symbol, holding.avg_entry_price))
            for symbol, holding in self._holdings.items()
        )

    def _buying_power(self) -> float:
        return max(0.0, self._equity() * self._margin_leverage - self._gross_exposure())

    def _accrue_short_fees(self, as_of: datetime) -> None:
        assert self._as_of is not None
        elapsed = (as_of - self._as_of).total_seconds()
        if elapsed <= 0 or self._short_borrow_apr <= 0:
            return
        for symbol, holding in self._holdings.items():
            if holding.qty >= 0:
                continue
            price = self._closes.get(symbol, holding.avg_entry_price)
            fee = abs(holding.qty) * price * self._short_borrow_apr * elapsed / _SECONDS_PER_YEAR
            self._cash -= fee
            self._short_fees[symbol] = self._short_fees.get(symbol, 0.0) + fee

    def _require_time(self) -> datetime:
        if self._as_of is None:
            raise BrokerError("simulated clock not set; call mark_to_market() first")
        return self._as_of
