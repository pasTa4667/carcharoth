from dataclasses import replace
from datetime import UTC, datetime

from carcharoth.domain.models import (
    MarketSnapshot,
    OrderStatus,
    Signal,
    SignalAction,
)
from carcharoth.engine.engine import TradingEngine
from tests.factories import make_account, make_bars, make_quote
from tests.fakes import (
    FakeAccountService,
    FakeMarketDataService,
    FakeOrderExecutor,
    FakeRiskManager,
    FakeStrategy,
    InMemoryOrderRepository,
    InMemoryPositionSnapshotRepository,
    InMemoryStrategyDecisionRepository,
    InMemoryTradeRepository,
    RaisingStrategy,
)

AS_OF = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)
SYMBOLS = ["AAPL", "MSFT"]


def make_snapshot(symbols: list[str] = SYMBOLS) -> MarketSnapshot:
    return MarketSnapshot(
        bars={symbol: make_bars([100.0] * 20, symbol) for symbol in symbols},
        quotes={symbol: make_quote(100.0, symbol) for symbol in symbols},
        as_of=AS_OF,
    )


def buy_signal(symbol: str) -> Signal:
    return Signal(symbol=symbol, action=SignalAction.BUY, strategy="fake", reason="test buy")


def build_engine(
    strategy: FakeStrategy,
    risk: FakeRiskManager | None = None,
    executor: FakeOrderExecutor | None = None,
) -> tuple[
    TradingEngine,
    FakeOrderExecutor,
    InMemoryStrategyDecisionRepository,
    InMemoryOrderRepository,
    InMemoryTradeRepository,
    InMemoryPositionSnapshotRepository,
]:
    executor = executor or FakeOrderExecutor()
    decisions = InMemoryStrategyDecisionRepository()
    orders = InMemoryOrderRepository()
    trades = InMemoryTradeRepository()
    snapshots = InMemoryPositionSnapshotRepository()
    engine = TradingEngine(
        market_data=FakeMarketDataService(make_snapshot()),
        account=FakeAccountService(make_account()),
        strategy=strategy,
        risk=risk or FakeRiskManager(),
        executor=executor,
        decisions_repo=decisions,
        orders_repo=orders,
        trades_repo=trades,
        snapshots_repo=snapshots,
        symbols=SYMBOLS,
        timeframe_minutes=5,
    )
    return engine, executor, decisions, orders, trades, snapshots


def test_approved_buy_submits_exactly_one_order() -> None:
    strategy = FakeStrategy({"AAPL": buy_signal("AAPL")})
    engine, executor, decisions, orders, _, _ = build_engine(strategy)

    engine.tick()

    assert len(executor.submitted) == 1
    assert executor.submitted[0].symbol == "AAPL"
    assert executor.submitted[0].client_order_id is not None
    assert len(orders.rows) == 1
    # both symbols evaluated, both decisions persisted
    assert strategy.evaluated == SYMBOLS
    assert len(decisions.saved) == 2


def test_hold_produces_decision_but_no_order() -> None:
    strategy = FakeStrategy({})  # everything holds
    engine, executor, decisions, orders, _, _ = build_engine(strategy)

    engine.tick()

    assert executor.submitted == []
    assert orders.rows == {}
    assert len(decisions.saved) == 2
    assert all(risk is None for _, risk, _ in decisions.saved)


def test_rejected_signal_is_persisted_but_not_executed() -> None:
    strategy = FakeStrategy({"AAPL": buy_signal("AAPL")})
    engine, executor, decisions, _, _, _ = build_engine(
        strategy, risk=FakeRiskManager(approve=False)
    )

    engine.tick()

    assert executor.submitted == []
    aapl_decisions = [risk for signal, risk, _ in decisions.saved if signal.symbol == "AAPL"]
    assert len(aapl_decisions) == 1
    assert aapl_decisions[0] is not None
    assert not aapl_decisions[0].approved


def test_failing_symbol_does_not_stop_others() -> None:
    strategy = RaisingStrategy({"MSFT": buy_signal("MSFT")}, raise_for={"AAPL"})
    engine, executor, _, _, _, _ = build_engine(strategy)

    engine.tick()

    assert strategy.evaluated == SYMBOLS
    assert len(executor.submitted) == 1
    assert executor.submitted[0].symbol == "MSFT"


def test_positions_snapshot_saved_every_tick() -> None:
    strategy = FakeStrategy({})
    engine, _, _, _, _, snapshots = build_engine(strategy)

    engine.tick()
    engine.tick()

    assert len(snapshots.snapshots) == 2
    assert snapshots.snapshots[0][0] == AS_OF


def test_fill_reconciliation_records_trade_once() -> None:
    strategy = FakeStrategy({"AAPL": buy_signal("AAPL")})
    engine, executor, _, orders, trades, _ = build_engine(strategy)

    engine.tick()
    broker_order_id = next(iter(orders.rows))
    executor.order_states[broker_order_id] = replace(
        orders.rows[broker_order_id],
        status=OrderStatus.FILLED,
        filled_qty=1,
        filled_avg_price=100.0,
        filled_at=AS_OF,
    )

    # strategy keeps signalling BUY, but risk sees it: use hold now to isolate reconciliation
    engine.tick()
    assert len(trades.fills) == 1
    assert orders.rows[broker_order_id].status is OrderStatus.FILLED

    engine.tick()  # reconciliation is idempotent
    assert len(trades.fills) == 1
