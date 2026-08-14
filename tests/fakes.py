"""In-memory fake implementations of every interface, for engine tests."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import UUID, uuid4

from carcharoth.analysis.metrics import RoundTrip
from carcharoth.config.run_config import RunConfig
from carcharoth.domain.models import (
    TERMINAL_ORDER_STATUSES,
    AccountState,
    AssignmentRecord,
    BacktestResult,
    Bar,
    BarSpec,
    DecisionRecord,
    EquityPoint,
    MarketSnapshot,
    MetricValue,
    OpenOrder,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Position,
    PositionSnapshot,
    Quote,
    RiskDecision,
    RunInfo,
    RunType,
    Signal,
    SignalAction,
    Timeframe,
    TradeRecord,
)
from carcharoth.interfaces import (
    AccountService,
    MarketClock,
    MarketDataService,
    OrderExecutor,
    RiskManager,
    Strategy,
)
from carcharoth.interfaces.regime_detector import RegimeDetector
from carcharoth.persistence.repositories import (
    AnalysisReader,
    BacktestMetricsRepository,
    OrderRepository,
    PositionSnapshotRepository,
    RegimeEvaluationRepository,
    RoundTripRepository,
    RunRepository,
    StrategyAssignmentRepository,
    StrategyDecisionRepository,
    TradeRepository,
)
from carcharoth.regime.models import RegimeAssessment, StrategyAssignment


class FakeMarketDataService(MarketDataService):
    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[list[str], BarSpec]] = []

    def get_snapshot(self, symbols: Sequence[str], spec: BarSpec) -> MarketSnapshot:
        self.calls.append((list(symbols), spec))
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

    def required_bars(self) -> BarSpec:
        return BarSpec(Timeframe.minutes(5), self._lookback)


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
    def __init__(
        self,
        order_states: dict[str, OrderResult] | None = None,
        submitted_at: datetime | None = None,
    ) -> None:
        self.submitted_at = submitted_at or datetime(2026, 7, 1, 15, 0, tzinfo=UTC)
        self.submitted: list[OrderRequest] = []
        self.canceled: list[str] = []
        #: broker_order_id -> result returned by get_order (reconciliation)
        self.order_states = order_states or {}
        #: when set, submit raises it instead of accepting the order
        self.submit_error: Exception | None = None
        #: when set, cancel_order raises it
        self.cancel_error: Exception | None = None

    def submit(self, request: OrderRequest) -> OrderResult:
        if self.submit_error is not None:
            raise self.submit_error
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
            submitted_at=self.submitted_at,
            filled_at=None,
        )

    def get_order(self, broker_order_id: str) -> OrderResult:
        return self.order_states[broker_order_id]

    def cancel_order(self, broker_order_id: str) -> None:
        if self.cancel_error is not None:
            raise self.cancel_error
        self.canceled.append(broker_order_id)


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
        return [
            broker_id
            for broker_id, row in self.rows.items()
            if row.status not in TERMINAL_ORDER_STATUSES
        ]

    def find_open_orders(self, symbol: str) -> list[OpenOrder]:
        return [
            OpenOrder(broker_order_id=row.broker_order_id, symbol=row.symbol, side=row.side)
            for row in self.rows.values()
            if row.symbol == symbol and row.status not in TERMINAL_ORDER_STATUSES
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
        self.equity_points: list[EquityPoint] = []

    def save_snapshot(self, timestamp: datetime, state: AccountState) -> None:
        self.snapshots.append((timestamp, list(state.positions.values())))
        self.equity_points.append(EquityPoint(timestamp=timestamp, equity=state.equity))


class InMemoryRegimeEvaluationRepository(RegimeEvaluationRepository):
    def __init__(self) -> None:
        self.saved: list[tuple[RegimeAssessment, datetime]] = []

    def save(self, assessment: RegimeAssessment, timestamp: datetime) -> None:
        self.saved.append((assessment, timestamp))


class InMemoryStrategyAssignmentRepository(StrategyAssignmentRepository):
    def __init__(self, current: dict[str, StrategyAssignment] | None = None) -> None:
        #: seeded "current" state returned by load_current (simulates restart)
        self.current = dict(current or {})
        self.saved: list[StrategyAssignment] = []

    def save(self, assignment: StrategyAssignment) -> None:
        self.saved.append(assignment)
        self.current[assignment.symbol] = assignment

    def load_current(self) -> dict[str, StrategyAssignment]:
        return dict(self.current)


class InMemoryRunRepository(RunRepository):
    def __init__(self) -> None:
        self.runs: dict[UUID, RunInfo] = {}

    def create(
        self,
        run_type: RunType,
        config: dict[str, Any],
        symbols: Sequence[str],
        started_at: datetime,
        backtest_start: datetime | None = None,
        backtest_end: datetime | None = None,
    ) -> UUID:
        run_id = uuid4()
        self.runs[run_id] = RunInfo(
            run_id=run_id,
            run_type=run_type,
            started_at=started_at,
            finished_at=None,
            symbols=list(symbols),
            backtest_start=backtest_start,
            backtest_end=backtest_end,
            config=dict(config),
        )
        return run_id

    def finish(self, run_id: UUID, finished_at: datetime) -> None:
        info = self.runs[run_id]
        self.runs[run_id] = RunInfo(
            run_id=info.run_id,
            run_type=info.run_type,
            started_at=info.started_at,
            finished_at=finished_at,
            symbols=info.symbols,
            backtest_start=info.backtest_start,
            backtest_end=info.backtest_end,
            config=info.config,
        )

    def get(self, run_id: UUID) -> RunInfo | None:
        return self.runs.get(run_id)

    def list_run_ids(self, run_type: RunType | None = None) -> list[UUID]:
        return [
            run_id
            for run_id, info in self.runs.items()
            if run_type is None or info.run_type is run_type
        ]

    def delete(self, run_id: UUID) -> None:
        self.runs.pop(run_id, None)


class InMemoryBacktestMetricsRepository(BacktestMetricsRepository):
    def __init__(self) -> None:
        self.saved: dict[UUID, list[MetricValue]] = {}

    def save_metrics(self, run_id: UUID, metrics: Sequence[MetricValue]) -> None:
        self.saved[run_id] = list(metrics)


class InMemoryAnalysisReader(AnalysisReader):
    def __init__(
        self,
        trades: dict[UUID, list[TradeRecord]] | None = None,
        equity: dict[UUID, list[EquityPoint]] | None = None,
        decisions: dict[UUID, list[DecisionRecord]] | None = None,
        assignments: dict[UUID, list[AssignmentRecord]] | None = None,
        position_snapshots: dict[UUID, list[PositionSnapshot]] | None = None,
    ) -> None:
        self.trades = trades or {}
        self.equity = equity or {}
        self.decisions = decisions or {}
        self.assignments = assignments or {}
        self.position_snapshots = position_snapshots or {}

    def list_trades(self, run_id: UUID) -> list[TradeRecord]:
        return list(self.trades.get(run_id, []))

    def list_equity(self, run_id: UUID) -> list[EquityPoint]:
        return list(self.equity.get(run_id, []))

    def list_decisions(self, run_id: UUID) -> list[DecisionRecord]:
        return list(self.decisions.get(run_id, []))

    def list_assignments(self, run_id: UUID) -> list[AssignmentRecord]:
        return list(self.assignments.get(run_id, []))

    def list_position_snapshots(self, run_id: UUID) -> list[PositionSnapshot]:
        return list(self.position_snapshots.get(run_id, []))


class InMemoryRoundTripRepository(RoundTripRepository):
    def __init__(self) -> None:
        self.saved: dict[UUID, list[RoundTrip]] = {}

    def save_all(self, run_id: UUID, round_trips: Sequence[RoundTrip]) -> None:
        self.saved[run_id] = list(round_trips)


class FakeBacktestFunc:
    """Scripted BacktestFunc: returns one metrics list per call (the last
    entry repeats once exhausted); an Exception entry is raised instead.
    Records every received config and the run_ids it handed out."""

    def __init__(self, scripted: Sequence[Sequence[MetricValue] | Exception]) -> None:
        self.scripted = list(scripted)
        self.calls: list[RunConfig] = []
        self.run_ids: list[UUID] = []

    def __call__(
        self,
        config: RunConfig,
        start: datetime,
        end_exclusive: datetime,
        symbols: Sequence[str],
    ) -> BacktestResult:
        self.calls.append(config)
        entry = self.scripted[min(len(self.calls) - 1, len(self.scripted) - 1)]
        if isinstance(entry, Exception):
            raise entry
        run_id = uuid4()
        self.run_ids.append(run_id)
        return BacktestResult(run_id=run_id, metrics=list(entry))


class FakeDetector(RegimeDetector):
    """Returns a scripted sequence of assessments per symbol; the last one
    sticks once the script is exhausted."""

    def __init__(
        self,
        assessments: dict[str, list[RegimeAssessment | None]] | None = None,
        lookback: int = 100,
    ) -> None:
        self._scripted = {s: list(seq) for s, seq in (assessments or {}).items()}
        self._sticky: dict[str, RegimeAssessment | None] = {}
        self._fake_lookback = lookback
        self.calls: list[str] = []

    def required_lookback(self) -> int:
        return self._fake_lookback

    def assess(self, symbol: str, bars: Sequence[Bar]) -> RegimeAssessment | None:
        self.calls.append(symbol)
        script = self._scripted.get(symbol)
        if script:
            self._sticky[symbol] = script.pop(0)
        return self._sticky.get(symbol)


class InMemoryByteStore:
    """Dict-backed ByteStore; counts round trips for cache-behavior asserts."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.mget_calls = 0
        self.mset_calls = 0

    def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        self.mget_calls += 1
        return [self.data.get(key) for key in keys]

    def set(self, key: str, value: bytes) -> None:
        self.data[key] = value

    def mset(self, items: Mapping[str, bytes]) -> None:
        self.mset_calls += 1
        self.data.update(items)

    def count_prefix(self, prefix: str) -> int:
        return sum(1 for key in self.data if key.startswith(prefix))

    def delete_prefix(self, prefix: str) -> int:
        matches = [key for key in self.data if key.startswith(prefix)]
        for key in matches:
            del self.data[key]
        return len(matches)

    def used_memory_bytes(self) -> int | None:
        return sum(len(value) for value in self.data.values())


class RaisingByteStore:
    """Every call raises, counting attempts — for ResilientByteStore tests."""

    def __init__(self) -> None:
        self.calls = 0

    def _boom(self) -> NoReturn:
        self.calls += 1
        raise ConnectionError("store down")

    def get(self, key: str) -> bytes | None:
        self._boom()

    def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        self._boom()

    def set(self, key: str, value: bytes) -> None:
        self._boom()

    def mset(self, items: Mapping[str, bytes]) -> None:
        self._boom()

    def count_prefix(self, prefix: str) -> int:
        self._boom()

    def delete_prefix(self, prefix: str) -> int:
        self._boom()

    def used_memory_bytes(self) -> int | None:
        self._boom()
