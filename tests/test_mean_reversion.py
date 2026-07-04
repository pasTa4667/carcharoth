import pytest

from carcharoth.domain.models import SignalAction
from carcharoth.strategies.mean_reversion import MeanReversionStrategy
from carcharoth.strategies.registry import build_strategy
from tests.factories import make_bars, make_position, make_quote

STABLE = [100.0, 101.0, 99.0, 100.0, 101.0, 99.0, 100.0, 101.0, 99.0, 100.0] * 2


def test_deep_dip_without_position_buys() -> None:
    strategy = MeanReversionStrategy(lookback=20)
    bars = make_bars([*STABLE[:-1], 80.0])
    signal = strategy.evaluate("AAPL", bars, make_quote(80.0), position=None)
    assert signal.action is SignalAction.BUY
    assert signal.indicators["zscore"] <= -2.0
    assert "below" in signal.reason


def test_reversion_with_position_sells() -> None:
    strategy = MeanReversionStrategy(lookback=20)
    signal = strategy.evaluate(
        "AAPL", make_bars(STABLE), make_quote(100.0), position=make_position()
    )
    assert signal.action is SignalAction.SELL


def test_dip_with_position_holds() -> None:
    strategy = MeanReversionStrategy(lookback=20)
    bars = make_bars([*STABLE[:-1], 80.0])
    signal = strategy.evaluate("AAPL", bars, make_quote(80.0), position=make_position())
    assert signal.action is SignalAction.HOLD


def test_normal_price_without_position_holds() -> None:
    strategy = MeanReversionStrategy(lookback=20)
    signal = strategy.evaluate("AAPL", make_bars(STABLE), make_quote(100.0), position=None)
    assert signal.action is SignalAction.HOLD


def test_short_history_holds() -> None:
    strategy = MeanReversionStrategy(lookback=20)
    signal = strategy.evaluate("AAPL", make_bars(STABLE[:5]), make_quote(100.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "insufficient history" in signal.reason


def test_flat_series_holds() -> None:
    strategy = MeanReversionStrategy(lookback=20)
    signal = strategy.evaluate("AAPL", make_bars([100.0] * 20), make_quote(50.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "zero std" in signal.reason


def test_missing_quote_falls_back_to_last_close() -> None:
    strategy = MeanReversionStrategy(lookback=20)
    bars = make_bars([*STABLE[:-1], 80.0])
    signal = strategy.evaluate("AAPL", bars, quote=None, position=None)
    assert signal.action is SignalAction.BUY
    assert signal.indicators["price"] == 80.0


def test_signal_carries_indicators() -> None:
    strategy = MeanReversionStrategy(lookback=20)
    signal = strategy.evaluate("AAPL", make_bars(STABLE), make_quote(100.0), position=None)
    assert set(signal.indicators) == {"zscore", "mean", "std", "price"}
    assert signal.strategy == "mean_reversion"


def test_registry_builds_with_params() -> None:
    strategy = build_strategy("mean_reversion", {"lookback": 30, "entry_z": -1.5})
    assert isinstance(strategy, MeanReversionStrategy)
    assert strategy.required_lookback() == 35


def test_registry_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="unknown strategy"):
        build_strategy("momentum", {})


def test_invalid_params_rejected() -> None:
    with pytest.raises(ValueError, match="lookback"):
        MeanReversionStrategy(lookback=1)
    with pytest.raises(ValueError, match="exit_z"):
        MeanReversionStrategy(entry_z=-1.0, exit_z=-2.0)
