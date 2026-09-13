from datetime import UTC, datetime

from carcharoth.domain.models import (
    BarSpec,
    ExposureIntent,
    MarketDataRequirements,
    MarketSnapshot,
    Position,
    Side,
    TargetExposure,
    Timeframe,
)
from carcharoth.interfaces.model_signal import ModelSignalGenerator
from carcharoth.quicktest.simulator import SimulationSettings, simulate_symbol
from tests.factories import make_bars

START = datetime(2026, 7, 1, tzinfo=UTC)
END = datetime(2026, 7, 2, tzinfo=UTC)
SESSION = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)


class ScriptedModel(ModelSignalGenerator):
    def __init__(self, script: dict[datetime, TargetExposure]) -> None:
        self.script = script
        self.positions: list[Position | None] = []

    def market_requirements(self) -> MarketDataRequirements:
        return MarketDataRequirements(BarSpec(Timeframe.minutes(5), 2), frozenset({"SPY"}))

    def generate(
        self, symbol: str, snapshot: MarketSnapshot, position: Position | None
    ) -> ExposureIntent:
        self.positions.append(position)
        timestamp = snapshot.bars[symbol][-1].timestamp
        return ExposureIntent(
            symbol,
            self.script.get(timestamp, TargetExposure.UNCHANGED),
            "scripted",
            "schema",
            "scripted",
            timestamp=timestamp,
        )


def test_long_entry_and_exit_fill_at_following_opens() -> None:
    bars = make_bars([100, 110, 120, 130], start=SESSION)
    model = ScriptedModel(
        {
            bars[0].timestamp: TargetExposure.LONG,
            bars[2].timestamp: TargetExposure.FLAT,
        }
    )
    result = simulate_symbol(
        model,
        "AAPL",
        bars,
        bars,
        START,
        END,
        SimulationSettings(10_000, 0.10),
    )
    assert [trade.side for trade in result.trades] == [Side.BUY, Side.SELL]
    assert [trade.price for trade in result.trades] == [110, 130]
    assert result.trades[0].timestamp == bars[1].timestamp


def test_short_profit_uses_signed_accounting() -> None:
    bars = make_bars([100, 100, 90, 80], start=SESSION)
    model = ScriptedModel(
        {
            bars[0].timestamp: TargetExposure.SHORT,
            bars[2].timestamp: TargetExposure.FLAT,
        }
    )
    result = simulate_symbol(
        model,
        "AAPL",
        bars,
        bars,
        START,
        END,
        SimulationSettings(10_000, 0.10),
    )
    assert [trade.side for trade in result.trades] == [Side.SELL, Side.BUY]
    assert result.equity[-1].equity > 10_000
    assert any(snapshot.unrealized_pnl > 0 for snapshot in result.snapshots)


def test_unchanged_never_flattens_existing_position() -> None:
    bars = make_bars([100, 100, 110], start=SESSION)
    model = ScriptedModel({bars[0].timestamp: TargetExposure.LONG})
    result = simulate_symbol(
        model,
        "AAPL",
        bars,
        bars,
        START,
        END,
        SimulationSettings(10_000, 0.10),
    )
    assert len(result.trades) == 1
    assert result.trades[0].side is Side.BUY


def test_preload_callers_prepare_targets_and_each_transformed_input(monkeypatch) -> None:
    from unittest.mock import Mock

    from carcharoth.config.app_config import FeatureSpecConfig, ModelConfig, ObjectiveConfig
    from carcharoth.config.quicktest_config import QuickTestConfig
    from carcharoth.permutation.runner import build_permutation_context
    from carcharoth.quicktest.runner import simulate_and_analyze

    model_config = ModelConfig(
        prepared_history=True, features=[FeatureSpecConfig(name="log_return", params={"period": 1})]
    )
    config = QuickTestConfig(
        symbols=["AAPL"], start=START.date(), end=START.date(), model=model_config
    )
    models = []

    def build(*args, **kwargs):
        model = ScriptedModel({})
        model.prepare_history = Mock()
        models.append(model)
        return model

    monkeypatch.setattr("carcharoth.quicktest.runner.build_signal_generator", build)
    monkeypatch.setattr("carcharoth.permutation.runner.build_signal_generator", build)
    bars = {"AAPL": make_bars([100, 101]), "SPY": make_bars([200, 201], symbol="SPY")}
    simulate_and_analyze(config, {}, bars)
    models[-1].prepare_history.assert_called_once_with(
        bars, symbols=["AAPL"], start=config.start_dt, end_exclusive=config.end_exclusive_dt
    )
    context = build_permutation_context(
        config, ObjectiveConfig(weights={"total_return": 1.0}), bars
    )
    transformed = {"AAPL": make_bars([101, 100]), "SPY": bars["SPY"]}
    context.simulate(transformed)
    models[-1].prepare_history.assert_called_once_with(
        transformed, symbols=["AAPL"], start=config.start_dt, end_exclusive=config.end_exclusive_dt
    )
    context.simulate(bars)
    assert len(models) == 3 and len({id(m) for m in models}) == 3
    config.model.prepared_history = False
    simulate_and_analyze(config, {}, bars)
    models[-1].prepare_history.assert_not_called()
