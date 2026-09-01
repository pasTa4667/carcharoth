from datetime import timedelta

from carcharoth.backtest.runner import BacktestRunner
from carcharoth.config.app_config import RiskConfig
from carcharoth.domain.models import Bar, ExposureIntent, TargetExposure
from carcharoth.engine.engine import TradingEngine
from carcharoth.risk.basic import BasicRiskManager
from carcharoth.services.backtest.broker import SimulatedBroker
from carcharoth.services.backtest.market_data import HistoricalMarketDataService
from tests.factories import BASE_TIME
from tests.fakes import (
    FakeModelSignalGenerator,
    InMemoryModelDecisionRepository,
    InMemoryOrderRepository,
    InMemoryPositionSnapshotRepository,
    InMemoryTradeRepository,
)


def test_runner_fills_signal_from_t_at_next_bar_open() -> None:
    bars = [
        Bar("AAPL", BASE_TIME + timedelta(minutes=5 * i), price, price, price, price, 1000)
        for i, price in enumerate([100.0, 110.0, 120.0])
    ]
    market = HistoricalMarketDataService(
        {"AAPL": bars, "SPY": bars}, spread_pct=0.0, tradable_symbols=["AAPL"]
    )
    broker = SimulatedBroker(100_000, 0.0, 0.0)
    orders = InMemoryOrderRepository()
    trades = InMemoryTradeRepository()
    model = FakeModelSignalGenerator(
        {"AAPL": ExposureIntent("AAPL", TargetExposure.LONG, "model", "schema", "long")},
        lookback=1,
    )
    engine = TradingEngine(
        market,
        broker,
        model,
        BasicRiskManager(RiskConfig(max_position_notional=100.0)),
        broker,
        InMemoryModelDecisionRepository(),
        orders,
        trades,
        InMemoryPositionSnapshotRepository(),
        ["AAPL"],
    )
    runner = BacktestRunner(
        engine,
        market,
        broker,
        orders,
        trades,
        broker,
        bars[0].timestamp,
        bars[-1].timestamp,
    )
    runner.run()
    assert len(trades.fills) == 1
    assert trades.fills[0].filled_avg_price == 110.0
    assert trades.fills[0].filled_at == bars[1].timestamp
