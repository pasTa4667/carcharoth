from dataclasses import replace
from datetime import UTC, datetime

from carcharoth.domain.models import (
    ExposureIntent,
    MarketSnapshot,
    OrderStatus,
    TargetExposure,
)
from carcharoth.engine.engine import TradingEngine
from tests.factories import make_account, make_bars, make_quote
from tests.fakes import (
    FakeAccountService,
    FakeMarketDataService,
    FakeModelSignalGenerator,
    FakeOrderExecutor,
    FakeRiskManager,
    InMemoryModelDecisionRepository,
    InMemoryOrderRepository,
    InMemoryPositionSnapshotRepository,
    InMemoryTradeRepository,
)

AS_OF = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)
SYMBOLS = ["AAPL", "MSFT"]


def intent(symbol: str, target: TargetExposure) -> ExposureIntent:
    return ExposureIntent(symbol, target, "model-1", "schema", "test", timestamp=AS_OF)


def build_engine(model: FakeModelSignalGenerator):
    snapshot = MarketSnapshot(
        bars={symbol: make_bars([100.0] * 20, symbol) for symbol in [*SYMBOLS, "SPY"]},
        quotes={symbol: make_quote(100.0, symbol) for symbol in SYMBOLS},
        as_of=AS_OF,
    )
    market = FakeMarketDataService(snapshot)
    executor = FakeOrderExecutor()
    decisions = InMemoryModelDecisionRepository()
    orders = InMemoryOrderRepository()
    trades = InMemoryTradeRepository()
    engine = TradingEngine(
        market_data=market,
        account=FakeAccountService(make_account()),
        model=model,
        risk=FakeRiskManager(),
        executor=executor,
        decisions_repo=decisions,
        orders_repo=orders,
        trades_repo=trades,
        snapshots_repo=InMemoryPositionSnapshotRepository(),
        symbols=SYMBOLS,
    )
    return engine, market, executor, decisions, orders, trades


def test_engine_fetches_context_but_only_generates_for_tradable_symbols() -> None:
    model = FakeModelSignalGenerator({})
    engine, market, _, decisions, _, _ = build_engine(model)
    engine.tick()
    assert market.calls == [(["AAPL", "MSFT", "SPY"], model.requirements.bars)]
    assert model.generated == SYMBOLS
    assert len(decisions.saved) == 2


def test_long_intent_submits_buy() -> None:
    model = FakeModelSignalGenerator({"AAPL": intent("AAPL", TargetExposure.LONG)})
    engine, _, executor, _, orders, _ = build_engine(model)
    engine.tick()
    assert len(executor.submitted) == 1
    assert executor.submitted[0].symbol == "AAPL"
    assert len(orders.rows) == 1


def test_unchanged_is_persisted_without_risk_or_order() -> None:
    model = FakeModelSignalGenerator({})
    engine, _, executor, decisions, _, _ = build_engine(model)
    engine.tick()
    assert executor.submitted == []
    assert all(risk is None for _, risk, _ in decisions.saved)


def test_failing_symbol_does_not_stop_remaining_symbols() -> None:
    model = FakeModelSignalGenerator({"MSFT": intent("MSFT", TargetExposure.LONG)})
    model.raise_for.add("AAPL")
    engine, _, executor, _, _, _ = build_engine(model)
    engine.tick()
    assert [request.symbol for request in executor.submitted] == ["MSFT"]


def test_fill_reconciliation_is_idempotent() -> None:
    model = FakeModelSignalGenerator({"AAPL": intent("AAPL", TargetExposure.LONG)})
    engine, _, executor, _, orders, trades = build_engine(model)
    engine.tick()
    broker_id = next(iter(orders.rows))
    executor.order_states[broker_id] = replace(
        orders.rows[broker_id],
        status=OrderStatus.FILLED,
        filled_qty=1,
        filled_avg_price=100.0,
        filled_at=AS_OF,
    )
    model.intents.clear()
    engine.tick()
    engine.tick()
    assert len(trades.fills) == 1
