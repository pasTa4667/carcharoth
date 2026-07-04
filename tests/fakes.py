"""In-memory fake implementations of every interface, for engine tests."""

from collections.abc import Iterable, Sequence
from datetime import datetime
from uuid import uuid4

from carcharoth.domain.models import (
    AccountState,
    Bar,
    MarketSnapshot,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Position,
    Quote,
    RiskDecision,
    Signal,
    SignalAction,
)
from carcharoth.interfaces import (
    AccountService,
    MarketClock,
    MarketDataService,
    OrderExecutor,
    RiskManager,
    Strategy,
)
from carcharoth.persistence.repositories import (
    OrderRepository,
    PositionSnapshotRepository,
    StrategyDecisionRepository,
    TradeRepository,
)


class FakeMarketDataService(MarketDataService):
    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[list[str], int, int]] = []

    def get_snapshot(
        self, symbols: Sequence[str], timeframe_minutes: int, lookback: int
    ) -> MarketSnapshot:
        self.calls.append((list(symbols), timeframe_minutes, lookback))
        return self.snapshot


class FakeAccountService(AccountService):
    def __init__(self, state: AccountState) -> None:
        self.state = state

    def get_account_state(self) -> AccountState:
        return self.state


class FakeStrategy(Strategy):
    name = "fake"

    def __init__(self, signals: dict[str, Signal], lookback: int = 20) -> None:
        self._signals = signals
        self._lookback = lookback
        self.evaluated: list[str] = []

    def evaluate(
        self, symbol: str, bars: list[Bar], quote: Quote | None, position: Position | None
    ) -> Signal:
        self.evaluated.append(symbol)
        signal = self._signals.get(symbol)
        if signal is None:
            return Signal(symbol=symbol, action=SignalAction.HOLD, strategy=self.name, reason="")
        return signal

    def required_lookback(self) -> int:
        return self._lookback


class RaisingStrategy(FakeStrategy):
    """Raises for the configured symbols, otherwise delegates to FakeStrategy."""

    def __init__(self, signals: dict[str, Signal], raise_for: set[str]) -> None:
        super().__init__(signals)
        self._raise_for = raise_for

    def evaluate(
        self, symbol: str, bars: list[Bar], quote: Quote | None, position: Position | None
    ) -> Signal:
        if symbol in self._raise_for:
            self.evaluated.append(symbol)
            raise RuntimeError(f"boom on {symbol}")
        return super().evaluate(symbol, bars, quote, position)


class FakeRiskManager(RiskManager):
    def __init__(self, approve: bool = True, qty: float = 1.0) -> None:
        self._approve = approve
        self._qty = qty
        self.assessed: list[Signal] = []

    def assess(self, signal: Signal, account: AccountState, quote: Quote) -> RiskDecision:
        self.assessed.append(signal)
        return RiskDecision(
            signal=signal,
            approved=self._approve,
            qty=self._qty if self._approve else 0,
            reason="approved" if self._approve else "rejected: test",
        )


class FakeOrderExecutor(OrderExecutor):
    def __init__(self, order_states: dict[str, OrderResult] | None = None) -> None:
        self.submitted: list[OrderRequest] = []
        #: broker_order_id -> result returned by get_order (reconciliation)
        self.order_states = order_states or {}

    def submit(self, request: OrderRequest) -> OrderResult:
        self.submitted.append(request)
        return OrderResult(
            broker_order_id=uuid4().hex,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            status=OrderStatus.ACCEPTED,
            filled_qty=0,
            filled_avg_price=None,
            submitted_at=datetime.now(),
            filled_at=None,
        )

    def get_order(self, broker_order_id: str) -> OrderResult:
        return self.order_states[broker_order_id]


class FakeClock(MarketClock):
    def __init__(self, open_: bool = True, until_open: float = 0.0) -> None:
        self._open = open_
        self._until_open = until_open

    def is_open(self) -> bool:
        return self._open

    def seconds_until_open(self) -> float:
        return self._until_open


class InMemoryStrategyDecisionRepository(StrategyDecisionRepository):
    def __init__(self) -> None:
        self.saved: list[tuple[Signal, RiskDecision | None, datetime]] = []

    def save(self, signal: Signal, risk: RiskDecision | None, timestamp: datetime) -> None:
        self.saved.append((signal, risk, timestamp))


class InMemoryOrderRepository(OrderRepository):
    def __init__(self) -> None:
        self.rows: dict[str, OrderResult] = {}

    def save_submitted(self, request: OrderRequest, result: OrderResult) -> None:
        self.rows[result.broker_order_id] = result

    def update_from_broker(self, result: OrderResult) -> None:
        self.rows[result.broker_order_id] = result

    def find_open_broker_order_ids(self) -> list[str]:
        from carcharoth.domain.models import TERMINAL_ORDER_STATUSES

        return [
            broker_id
            for broker_id, row in self.rows.items()
            if row.status not in TERMINAL_ORDER_STATUSES
        ]


class InMemoryTradeRepository(TradeRepository):
    def __init__(self) -> None:
        self.fills: list[OrderResult] = []

    def save_fill(self, result: OrderResult) -> None:
        self.fills.append(result)

    def exists_for_order(self, broker_order_id: str) -> bool:
        return any(fill.broker_order_id == broker_order_id for fill in self.fills)


class InMemoryPositionSnapshotRepository(PositionSnapshotRepository):
    def __init__(self) -> None:
        self.snapshots: list[tuple[datetime, list[Position]]] = []

    def save_snapshot(self, timestamp: datetime, positions: Iterable[Position]) -> None:
        self.snapshots.append((timestamp, list(positions)))
