"""In-memory adapters shared by tests."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import UUID, uuid4

from carcharoth.analysis.metrics import RoundTrip
from carcharoth.config.run_config import RunConfig
from carcharoth.domain.models import (
    TERMINAL_ORDER_STATUSES,
    AccountState,
    BacktestResult,
    BarSpec,
    DecisionRecord,
    EquityPoint,
    ExposureIntent,
    MarketDataRequirements,
    MarketSnapshot,
    MetricValue,
    OpenOrder,
    OrderRequest,
    OrderResult,
    OrderStatus,
    PerformanceSnapshot,
    Position,
    PositionSnapshot,
    Quote,
    RiskDecision,
    RunInfo,
    RunType,
    Side,
    TargetExposure,
    Timeframe,
    TradeRecord,
)
from carcharoth.interfaces import (
    AccountService,
    MarketClock,
    MarketDataService,
    ModelSignalGenerator,
    OrderExecutor,
    RiskManager,
)
from carcharoth.persistence.repositories import (
    AnalysisReader,
    BacktestMetricsRepository,
    ModelDecisionRepository,
    OrderRepository,
    PerformanceRepository,
    PositionSnapshotRepository,
    RoundTripRepository,
    RunRepository,
    TradeRepository,
)


class InMemoryPerformanceRepository(PerformanceRepository):
    def __init__(self) -> None:
        self.snapshots: list[PerformanceSnapshot] = []

    def publish(self, snapshot: PerformanceSnapshot) -> None:
        self.snapshots.append(snapshot)


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


class FakeModelSignalGenerator(ModelSignalGenerator):
    def __init__(
        self,
        intents: Mapping[str, ExposureIntent],
        lookback: int = 20,
        context_symbols: frozenset[str] = frozenset({"SPY"}),
    ) -> None:
        self.intents = dict(intents)
        self.requirements = MarketDataRequirements(
            BarSpec(Timeframe.minutes(5), lookback), context_symbols
        )
        self.generated: list[str] = []
        self.raise_for: set[str] = set()

    def market_requirements(self) -> MarketDataRequirements:
        return self.requirements

    def generate(
        self, symbol: str, snapshot: MarketSnapshot, position: Position | None
    ) -> ExposureIntent:
        self.generated.append(symbol)
        if symbol in self.raise_for:
            raise RuntimeError("boom")
        return self.intents.get(
            symbol,
            ExposureIntent(symbol, TargetExposure.UNCHANGED, "fake", "schema", "test"),
        )


class FakeRiskManager(RiskManager):
    def __init__(self, approve: bool = True, qty: float = 1.0) -> None:
        self.approve = approve
        self.qty = qty
        self.assessed: list[ExposureIntent] = []

    def assess(self, intent: ExposureIntent, account: AccountState, quote: Quote) -> RiskDecision:
        self.assessed.append(intent)
        side = Side.BUY if intent.target is TargetExposure.LONG else Side.SELL
        return RiskDecision(
            intent=intent,
            approved=self.approve,
            qty=self.qty if self.approve else 0,
            side=side if self.approve else None,
            reason="approved" if self.approve else "rejected",
        )


class FakeOrderExecutor(OrderExecutor):
    def __init__(self) -> None:
        self.submitted: list[OrderRequest] = []
        self.canceled: list[str] = []
        self.order_states: dict[str, OrderResult] = {}
        self.submit_error: Exception | None = None

    def submit(self, request: OrderRequest) -> OrderResult:
        if self.submit_error is not None:
            raise self.submit_error
        self.submitted.append(request)
        result = OrderResult(
            broker_order_id=uuid4().hex,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            status=OrderStatus.ACCEPTED,
            filled_qty=0,
            filled_avg_price=None,
            submitted_at=datetime(2026, 7, 1, 15, 0, tzinfo=UTC),
            filled_at=None,
        )
        self.order_states[result.broker_order_id] = result
        return result

    def get_order(self, broker_order_id: str) -> OrderResult:
        return self.order_states[broker_order_id]

    def cancel_order(self, broker_order_id: str) -> None:
        self.canceled.append(broker_order_id)


class FakeClock(MarketClock):
    def is_open(self) -> bool:
        return True

    def seconds_until_open(self) -> float:
        return 0.0


class InMemoryModelDecisionRepository(ModelDecisionRepository):
    def __init__(self) -> None:
        self.saved: list[tuple[ExposureIntent, RiskDecision | None, datetime]] = []

    def save(self, intent: ExposureIntent, risk: RiskDecision | None, timestamp: datetime) -> None:
        self.saved.append((intent, risk, timestamp))


class InMemoryOrderRepository(OrderRepository):
    def __init__(self) -> None:
        self.rows: dict[str, OrderResult] = {}

    def save_submitted(self, request: OrderRequest, result: OrderResult) -> None:
        self.rows[result.broker_order_id] = result

    def update_from_broker(self, result: OrderResult) -> None:
        self.rows[result.broker_order_id] = result

    def find_open_broker_order_ids(self) -> list[str]:
        return [key for key, row in self.rows.items() if row.status not in TERMINAL_ORDER_STATUSES]

    def find_open_orders(self, symbol: str) -> list[OpenOrder]:
        return [
            OpenOrder(row.broker_order_id, row.symbol, row.side)
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

    def save_snapshot(self, timestamp: datetime, state: AccountState) -> None:
        self.snapshots.append((timestamp, list(state.positions.values())))


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
        loop_context: dict[str, Any] | None = None,
    ) -> UUID:
        run_id = uuid4()
        self.runs[run_id] = RunInfo(
            run_id,
            run_type,
            started_at,
            None,
            list(symbols),
            backtest_start,
            backtest_end,
            config,
            loop_context,
        )
        return run_id

    def finish(self, run_id: UUID, finished_at: datetime) -> None:
        current = self.runs[run_id]
        self.runs[run_id] = RunInfo(
            current.run_id,
            current.run_type,
            current.started_at,
            finished_at,
            current.symbols,
            current.backtest_start,
            current.backtest_end,
            current.config,
            current.loop_context,
        )

    def get(self, run_id: UUID) -> RunInfo | None:
        return self.runs.get(run_id)

    def list_run_ids(self, run_type: RunType | None = None) -> list[UUID]:
        return [
            key
            for key, value in self.runs.items()
            if run_type is None or value.run_type is run_type
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
        position_snapshots: dict[UUID, list[PositionSnapshot]] | None = None,
    ) -> None:
        self.trades = trades or {}
        self.equity = equity or {}
        self.decisions = decisions or {}
        self.position_snapshots = position_snapshots or {}

    def list_trades(self, run_id: UUID) -> list[TradeRecord]:
        return list(self.trades.get(run_id, []))

    def list_equity(self, run_id: UUID) -> list[EquityPoint]:
        return list(self.equity.get(run_id, []))

    def list_decisions(self, run_id: UUID) -> list[DecisionRecord]:
        return list(self.decisions.get(run_id, []))

    def list_position_snapshots(self, run_id: UUID) -> list[PositionSnapshot]:
        return list(self.position_snapshots.get(run_id, []))


class InMemoryRoundTripRepository(RoundTripRepository):
    def __init__(self) -> None:
        self.saved: dict[UUID, list[RoundTrip]] = {}

    def save_all(self, run_id: UUID, round_trips: Sequence[RoundTrip]) -> None:
        self.saved[run_id] = list(round_trips)


class FakeBacktestFunc:
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
        return BacktestResult(run_id, list(entry))


class InMemoryByteStore:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.mget_calls = 0
        self.mset_calls = 0
        self.read_keys: list[str] = []
        self.bytes_read = 0
        self.bytes_written = 0

    def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        self.mget_calls += 1
        self.read_keys.extend(keys)
        values = [self.data.get(key) for key in keys]
        self.bytes_read += sum(len(value) for value in values if value is not None)
        return values

    def set(self, key: str, value: bytes) -> None:
        self.data[key] = value

    def mset(self, items: Mapping[str, bytes]) -> None:
        self.mset_calls += 1
        self.bytes_written += sum(map(len, items.values()))
        self.data.update(items)

    def count_prefix(self, prefix: str) -> int:
        return sum(key.startswith(prefix) for key in self.data)

    def delete_prefix(self, prefix: str) -> int:
        keys = [key for key in self.data if key.startswith(prefix)]
        for key in keys:
            del self.data[key]
        return len(keys)

    def used_memory_bytes(self) -> int | None:
        return sum(map(len, self.data.values()))


class RaisingByteStore:
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
