"""End-to-end backtest loop with the real engine, sim broker and fakes."""

from datetime import timedelta

from carcharoth.backtest.runner import BacktestRunner
from carcharoth.domain.models import (
    Bar,
    OrderStatus,
    Position,
    Quote,
    Signal,
    SignalAction,
    TradeIntent,
)
from carcharoth.engine.engine import TradingEngine
from carcharoth.engine.strategy_provider import SingleStrategyProvider
from carcharoth.services.backtest.broker import SimulatedBroker
from carcharoth.services.backtest.market_data import HistoricalMarketDataService
from tests.factories import BASE_TIME, make_bars
from tests.fakes import (
    FakeRiskManager,
    FakeStrategy,
    InMemoryOrderRepository,
    InMemoryPositionSnapshotRepository,
    InMemoryStrategyDecisionRepository,
    InMemoryTradeRepository,
)

N_BARS = 10
START = BASE_TIME
END = BASE_TIME + timedelta(minutes=5 * (N_BARS - 1))


class ShortRoundTripStrategy(FakeStrategy):
    def evaluate(
        self,
        symbol: str,
        bars: list[Bar],
        quote: Quote | None,
        position: Position | None,
    ) -> Signal:
        self.evaluated.append(symbol)
        intent = TradeIntent.OPEN_SHORT if position is None else TradeIntent.CLOSE_SHORT
        return Signal(symbol, intent, self.name, "short lifecycle")


class _Harness:
    def __init__(
        self,
        signal_action: SignalAction = SignalAction.BUY,
        strategy: FakeStrategy | None = None,
    ) -> None:
        # BASE_TIME is 11:00 New York on a weekday, so all bars are in-session.
        bars = make_bars([100.0 + i for i in range(N_BARS)])
        self.market_data = HistoricalMarketDataService({"AAPL": bars}, spread_pct=0.0)
        self.broker = SimulatedBroker(initial_capital=100_000.0, spread_pct=0.0, slippage_pct=0.0)
        self.decisions = InMemoryStrategyDecisionRepository()
        self.orders = InMemoryOrderRepository()
        self.trades = InMemoryTradeRepository()
        self.snapshots = InMemoryPositionSnapshotRepository()
        signal = Signal(symbol="AAPL", action=signal_action, strategy="fake", reason="test")
        strategy = strategy or FakeStrategy({"AAPL": signal}, lookback=1)
        engine = TradingEngine(
            market_data=self.market_data,
            account=self.broker,
            strategies=SingleStrategyProvider(strategy),
            risk=FakeRiskManager(approve=True, qty=1.0),
            executor=self.broker,
            decisions_repo=self.decisions,
            orders_repo=self.orders,
            trades_repo=self.trades,
            snapshots_repo=self.snapshots,
            symbols=["AAPL"],
        )
        self.runner = BacktestRunner(
            engine=engine,
            market_data=self.market_data,
            broker=self.broker,
            orders_repo=self.orders,
            trades_repo=self.trades,
            executor=self.broker,
            start=START,
            end=END,
        )


def test_one_tick_per_bar_with_snapshots_and_decisions() -> None:
    harness = _Harness(signal_action=SignalAction.HOLD)
    harness.runner.run()

    assert len(harness.snapshots.snapshots) == N_BARS
    assert len(harness.decisions.saved) == N_BARS
    assert len(harness.snapshots.equity_points) == N_BARS
    assert all(point.equity == 100_000.0 for point in harness.snapshots.equity_points)


def test_repeated_open_signal_does_not_pyramid_and_final_fill_is_reconciled() -> None:
    harness = _Harness(signal_action=SignalAction.BUY)
    harness.runner.run()

    # The first order fills and later open-long signals are rejected because
    # the position is no longer flat. The runner still performs final reconcile.
    assert len(harness.trades.fills) == 1
    assert all(fill.status is OrderStatus.FILLED for fill in harness.trades.fills)
    assert not harness.orders.find_open_broker_order_ids()
    assert harness.broker.get_account_state().positions["AAPL"].qty == 1


def test_short_open_and_cover_flow_through_full_backtest_engine() -> None:
    harness = _Harness(strategy=ShortRoundTripStrategy({}, lookback=1))
    harness.runner.run()

    assert len(harness.trades.fills) == N_BARS
    assert [fill.side.value for fill in harness.trades.fills] == ["sell", "buy"] * (N_BARS // 2)
    assert harness.broker.get_account_state().positions == {}


def test_no_wall_clock_leaks_into_persisted_timestamps() -> None:
    harness = _Harness(signal_action=SignalAction.BUY)
    harness.runner.run()

    persisted = (
        [timestamp for timestamp, _ in harness.snapshots.snapshots]
        + [point.timestamp for point in harness.snapshots.equity_points]
        + [timestamp for _, _, timestamp in harness.decisions.saved]
        + [fill.filled_at or fill.submitted_at for fill in harness.trades.fills]
    )
    assert persisted
    assert all(START <= timestamp <= END for timestamp in persisted)


def test_equity_curve_reflects_position_gains() -> None:
    harness = _Harness(signal_action=SignalAction.BUY)
    harness.runner.run()

    # price rises while the one non-pyramided position remains open
    equity = [point.equity for point in harness.snapshots.equity_points]
    assert equity[-1] > equity[0]
    assert equity == sorted(equity)


def test_empty_range_is_a_noop() -> None:
    harness = _Harness()
    harness.runner._start = END + timedelta(days=1)
    harness.runner._end = END + timedelta(days=2)
    harness.runner.run()
    assert harness.snapshots.snapshots == []
