"""In-memory broker simulation: account state + instant market-order fills.

Fill contract (mirrors how `TradingEngine._reconcile_fills` records trades):
`submit()` mutates cash/positions immediately but returns ACCEPTED with no
fill details; `get_order()` then reports the order FILLED at the simulated
fill time. If `submit()` returned FILLED directly, the order would be stored
terminal and the engine would never write a trade row.

The backtest runner drives time: `mark_to_market(as_of, closes)` must be
called before each tick so fills and valuations use that bar's prices.
"""

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
    Side,
)
from carcharoth.interfaces.account import AccountService
from carcharoth.interfaces.execution import OrderExecutor
from carcharoth.strategies.session import MARKET_TZ

logger = logging.getLogger(__name__)


@dataclass
class _Holding:
    qty: float
    avg_entry_price: float


class SimulatedBroker(AccountService, OrderExecutor):
    def __init__(self, initial_capital: float, spread_pct: float, slippage_pct: float) -> None:
        self._cash = initial_capital
        self._spread_pct = spread_pct
        self._slippage_pct = slippage_pct
        self._holdings: dict[str, _Holding] = {}
        self._closes: dict[str, float] = {}
        self._as_of: datetime | None = None
        self._last_equity = initial_capital
        self._session_date: date | None = None
        #: broker_order_id -> the completed fill reported by get_order()
        self._fills: dict[str, OrderResult] = {}

    def mark_to_market(self, as_of: datetime, closes: dict[str, float]) -> None:
        """Set the simulated time and current prices. Rolls `last_equity` at
        each market-timezone session change so the risk manager's daily-loss
        rule resets per session, as it does live."""
        self._as_of = as_of
        session_date = as_of.astimezone(MARKET_TZ).date()
        if self._session_date is not None and session_date != self._session_date:
            # equity at the previous session's last prices, like Alpaca's
            # last_equity (previous end-of-day equity)
            self._last_equity = self._equity()
        self._session_date = session_date
        self._closes.update(closes)

    def get_account_state(self) -> AccountState:
        positions = {symbol: self._position(symbol) for symbol in self._holdings}
        return AccountState(
            equity=self._equity(),
            cash=self._cash,
            buying_power=self._cash,  # cash account, no margin
            last_equity=self._last_equity,
            positions=positions,
        )

    def submit(self, request: OrderRequest) -> OrderResult:
        as_of = self._require_time()
        close = self._closes.get(request.symbol)
        if close is None:
            raise BrokerError(f"no price for {request.symbol} at {as_of}")
        if request.side is Side.BUY:
            fill_price = self._buy(request, close)
        else:
            fill_price = self._sell(request, close)

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
        )
        # ACCEPTED, not FILLED: the engine records the trade when the next
        # reconcile pass sees the order transition to FILLED (see module doc).
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
        # Simulated orders fill instantly; there is never anything to cancel.
        logger.debug("cancel_order(%s) ignored: simulated fills are instant", broker_order_id)

    def _buy(self, request: OrderRequest, close: float) -> float:
        fill_price = close * (1 + self._spread_pct / 2) * (1 + self._slippage_pct)
        cost = fill_price * request.qty
        if cost > self._cash:
            raise BrokerError(
                f"insufficient cash for {request.symbol}: need {cost:.2f}, have {self._cash:.2f}"
            )
        holding = self._holdings.get(request.symbol)
        if holding is None:
            self._holdings[request.symbol] = _Holding(qty=request.qty, avg_entry_price=fill_price)
        else:
            total_qty = holding.qty + request.qty
            holding.avg_entry_price = (
                holding.avg_entry_price * holding.qty + fill_price * request.qty
            ) / total_qty
            holding.qty = total_qty
        self._cash -= cost
        return fill_price

    def _sell(self, request: OrderRequest, close: float) -> float:
        holding = self._holdings.get(request.symbol)
        if holding is None or holding.qty < request.qty:
            held = holding.qty if holding else 0
            raise BrokerError(f"cannot sell {request.qty} {request.symbol}, holding {held}")
        fill_price = close * (1 - self._spread_pct / 2) * (1 - self._slippage_pct)
        self._cash += fill_price * request.qty
        holding.qty -= request.qty
        if holding.qty == 0:
            del self._holdings[request.symbol]
        return fill_price

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

    def _require_time(self) -> datetime:
        if self._as_of is None:
            raise BrokerError("simulated clock not set; call mark_to_market() first")
        return self._as_of
