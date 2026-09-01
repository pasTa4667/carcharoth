"""Signed-position broker with next-bar-open market fills."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from uuid import uuid4

from carcharoth.domain.errors import BrokerError
from carcharoth.domain.models import (
    AccountState,
    Bar,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Position,
    Side,
)
from carcharoth.features.session import MARKET_TZ
from carcharoth.interfaces.account import AccountService
from carcharoth.interfaces.execution import OrderExecutor

logger = logging.getLogger(__name__)


@dataclass
class _Holding:
    qty: float
    avg_entry_price: float


class SimulatedBroker(AccountService, OrderExecutor):
    def __init__(
        self,
        initial_capital: float,
        spread_pct: float,
        slippage_pct: float,
        margin_multiplier: float = 2.0,
    ) -> None:
        self._cash = initial_capital
        self._spread_pct = spread_pct
        self._slippage_pct = slippage_pct
        self._margin_multiplier = margin_multiplier
        self._holdings: dict[str, _Holding] = {}
        self._closes: dict[str, float] = {}
        self._as_of: datetime | None = None
        self._last_equity = initial_capital
        self._session_date: date | None = None
        self._orders: dict[str, OrderResult] = {}
        self._pending: dict[str, OrderRequest] = {}

    def mark_to_market(self, as_of: datetime, bars: Mapping[str, Bar]) -> None:
        """Fill prior accepted orders at this bar's open, then mark at close."""
        session_date = as_of.astimezone(MARKET_TZ).date()
        if self._session_date is not None and session_date != self._session_date:
            self._last_equity = self._equity()
        self._session_date = session_date
        self._as_of = as_of
        self._fill_pending(bars)
        self._closes.update({symbol: bar.close for symbol, bar in bars.items()})

    def get_account_state(self) -> AccountState:
        positions = {symbol: self._position(symbol) for symbol in self._holdings}
        equity = self._equity()
        gross = sum(
            abs(holding.qty) * self._price(symbol, holding)
            for symbol, holding in self._holdings.items()
        )
        buying_power = max(0.0, equity * self._margin_multiplier - gross)
        return AccountState(
            equity=equity,
            cash=self._cash,
            buying_power=buying_power,
            last_equity=self._last_equity,
            positions=positions,
        )

    def submit(self, request: OrderRequest) -> OrderResult:
        as_of = self._require_time()
        if request.qty <= 0:
            raise BrokerError("order quantity must be positive")
        if request.symbol not in self._closes:
            raise BrokerError(f"no price for {request.symbol} at {as_of}")
        broker_order_id = uuid4().hex
        accepted = OrderResult(
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
        self._orders[broker_order_id] = accepted
        self._pending[broker_order_id] = request
        return accepted

    def get_order(self, broker_order_id: str) -> OrderResult:
        try:
            return self._orders[broker_order_id]
        except KeyError:
            raise BrokerError(f"unknown order {broker_order_id}") from None

    def cancel_order(self, broker_order_id: str) -> None:
        current = self.get_order(broker_order_id)
        if current.status is not OrderStatus.ACCEPTED:
            return
        self._pending.pop(broker_order_id, None)
        self._orders[broker_order_id] = self._terminal(current, OrderStatus.CANCELED)

    def cancel_pending(self) -> None:
        """Cancel orders left without a next bar at the end of replay."""
        for broker_order_id in list(self._pending):
            self.cancel_order(broker_order_id)

    def _fill_pending(self, bars: Mapping[str, Bar]) -> None:
        as_of = self._require_time()
        for broker_order_id, request in list(self._pending.items()):
            bar = bars.get(request.symbol)
            if bar is None:
                continue
            current = self._orders[broker_order_id]
            try:
                fill_price = self._fill_price(request.side, bar.open)
                self._validate_buying_power(request, fill_price)
                self._apply_fill(request, fill_price)
            except BrokerError:
                logger.exception("simulated order %s rejected at fill", broker_order_id)
                self._orders[broker_order_id] = self._terminal(current, OrderStatus.REJECTED)
            else:
                self._orders[broker_order_id] = OrderResult(
                    broker_order_id=broker_order_id,
                    client_order_id=current.client_order_id,
                    symbol=request.symbol,
                    side=request.side,
                    qty=request.qty,
                    status=OrderStatus.FILLED,
                    filled_qty=request.qty,
                    filled_avg_price=fill_price,
                    submitted_at=current.submitted_at,
                    filled_at=as_of,
                )
            del self._pending[broker_order_id]

    def _validate_buying_power(self, request: OrderRequest, price: float) -> None:
        holding = self._holdings.get(request.symbol)
        signed_delta = request.qty if request.side is Side.BUY else -request.qty
        current_qty = holding.qty if holding else 0.0
        # Reductions are always allowed; only an increase in absolute exposure
        # consumes buying power.
        if abs(current_qty + signed_delta) <= abs(current_qty):
            return
        if request.qty * price > self.get_account_state().buying_power:
            raise BrokerError("insufficient simulated margin buying power")

    def _apply_fill(self, request: OrderRequest, fill_price: float) -> None:
        delta = request.qty if request.side is Side.BUY else -request.qty
        self._cash -= delta * fill_price
        holding = self._holdings.get(request.symbol)
        if holding is None:
            self._holdings[request.symbol] = _Holding(delta, fill_price)
            return
        old_qty = holding.qty
        new_qty = old_qty + delta
        if new_qty == 0:
            del self._holdings[request.symbol]
            return
        if old_qty * delta > 0:
            holding.avg_entry_price = (
                holding.avg_entry_price * abs(old_qty) + fill_price * abs(delta)
            ) / abs(new_qty)
        elif old_qty * new_qty < 0:
            # Defensive crossing support; the risk manager normally closes first.
            holding.avg_entry_price = fill_price
        holding.qty = new_qty

    def _fill_price(self, side: Side, open_price: float) -> float:
        if open_price <= 0:
            raise BrokerError("invalid next-bar open")
        if side is Side.BUY:
            return open_price * (1 + self._spread_pct / 2) * (1 + self._slippage_pct)
        return open_price * (1 - self._spread_pct / 2) * (1 - self._slippage_pct)

    @staticmethod
    def _terminal(current: OrderResult, status: OrderStatus) -> OrderResult:
        return OrderResult(
            broker_order_id=current.broker_order_id,
            client_order_id=current.client_order_id,
            symbol=current.symbol,
            side=current.side,
            qty=current.qty,
            status=status,
            filled_qty=0,
            filled_avg_price=None,
            submitted_at=current.submitted_at,
            filled_at=None,
        )

    def _position(self, symbol: str) -> Position:
        holding = self._holdings[symbol]
        price = self._price(symbol, holding)
        return Position(
            symbol=symbol,
            qty=holding.qty,
            avg_entry_price=holding.avg_entry_price,
            market_value=holding.qty * price,
            unrealized_pnl=(price - holding.avg_entry_price) * holding.qty,
            current_price=price,
        )

    def _price(self, symbol: str, holding: _Holding) -> float:
        return self._closes.get(symbol, holding.avg_entry_price)

    def _equity(self) -> float:
        return self._cash + sum(
            holding.qty * self._price(symbol, holding) for symbol, holding in self._holdings.items()
        )

    def _require_time(self) -> datetime:
        if self._as_of is None:
            raise BrokerError("simulated clock not set; call mark_to_market() first")
        return self._as_of
