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
