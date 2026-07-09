from datetime import UTC, datetime
from typing import Any

import pytest

from carcharoth.domain.models import SignalAction, Timeframe
from carcharoth.strategies.mean_reversion import MeanReversionStrategy
from carcharoth.strategies.registry import build_strategy
from tests.factories import make_bars, make_position, make_quote

STABLE = [100.0, 101.0, 99.0, 100.0, 101.0, 99.0, 100.0, 101.0, 99.0, 100.0] * 2

# Long-ago cheaper bars keep the trend EMA (~94.7) below the recent rolling
# mean (~99.5) but above the dipped price (90): a pullback within an uptrend,
# in the shape it takes live — the dip itself pierces the trend line.
DIP_IN_UPTREND = [85.0] * 10 + [*STABLE[:-1], 90.0]

# Long-ago expensive bars keep the trend EMA above the recent rolling mean, so
# the final dip reads as a continuation of a downtrend.
DIP_IN_DOWNTREND = [200.0] * 10 + [*STABLE[:-1], 90.0]


def make_strategy(**overrides: Any) -> MeanReversionStrategy:
    """Strategy with filter periods small enough for compact test fixtures."""
    params: dict[str, Any] = {
        "lookback": 20,
        "trend_ema_period": 30,
        "rsi_period": 5,
        "rsi_entry_max": 99.0,
        "atr_period": 5,
    }
    params.update(overrides)
    return MeanReversionStrategy(**params)


def test_deep_dip_without_position_buys() -> None:
    strategy = make_strategy()
    bars = make_bars(DIP_IN_UPTREND)
    signal = strategy.evaluate("AAPL", bars, make_quote(90.0), position=None)
    assert signal.action is SignalAction.BUY
    assert signal.indicators["zscore"] <= -2.0
    assert "below" in signal.reason


def test_reversion_with_position_sells() -> None:
    strategy = MeanReversionStrategy(lookback=20)
    signal = strategy.evaluate(
        "AAPL", make_bars(STABLE), make_quote(100.0), position=make_position()
    )
    assert signal.action is SignalAction.SELL
    assert "mean reverted" in signal.reason


def test_dip_within_stop_with_position_holds() -> None:
    strategy = make_strategy()
    bars = make_bars([*STABLE[:-1], 98.0])
    signal = strategy.evaluate("AAPL", bars, make_quote(98.0), position=make_position())
    assert signal.action is SignalAction.HOLD


def test_normal_price_without_position_holds() -> None:
    strategy = make_strategy()
    signal = strategy.evaluate("AAPL", make_bars(STABLE), make_quote(100.0), position=None)
    assert signal.action is SignalAction.HOLD


def test_short_history_holds() -> None:
    strategy = make_strategy()
    signal = strategy.evaluate("AAPL", make_bars(STABLE[:5]), make_quote(100.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "insufficient history" in signal.reason


def test_flat_series_holds() -> None:
    strategy = make_strategy()
    signal = strategy.evaluate("AAPL", make_bars([100.0] * 20), make_quote(50.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "zero std" in signal.reason


def test_missing_quote_falls_back_to_last_close() -> None:
    strategy = make_strategy()
    bars = make_bars(DIP_IN_UPTREND)
    signal = strategy.evaluate("AAPL", bars, quote=None, position=None)
    assert signal.action is SignalAction.BUY
    assert signal.indicators["price"] == 90.0


def test_signal_carries_indicators() -> None:
    strategy = make_strategy()
    signal = strategy.evaluate("AAPL", make_bars(STABLE), make_quote(100.0), position=None)
    assert set(signal.indicators) == {"zscore", "mean", "std", "price"}
    assert signal.strategy == "mean_reversion"


def test_stop_loss_triggers_sell() -> None:
    strategy = make_strategy(atr_stop_multiplier=2.0)
    bars = make_bars(STABLE, hl_range=1.0)
    signal = strategy.evaluate("AAPL", bars, make_quote(95.0), position=make_position(price=100.0))
    assert signal.action is SignalAction.SELL
    assert "stop loss" in signal.reason
    assert signal.indicators["stop_price"] > 95.0


def test_price_above_stop_does_not_trigger_stop() -> None:
    strategy = make_strategy(atr_stop_multiplier=2.0)
    bars = make_bars(STABLE, hl_range=1.0)
    signal = strategy.evaluate("AAPL", bars, make_quote(99.0), position=make_position(price=100.0))
    assert signal.action is SignalAction.HOLD


def test_stop_loss_works_with_short_history() -> None:
    strategy = make_strategy()
    bars = make_bars(STABLE[:8], hl_range=1.0)
    signal = strategy.evaluate("AAPL", bars, make_quote(90.0), position=make_position(price=100.0))
    assert signal.action is SignalAction.SELL
    assert "stop loss" in signal.reason


def test_end_of_day_flattens_position() -> None:
    # 20 stable bars ending at 15:50 ET (19:50 UTC on an EDT date), 10 minutes
    # before the close; the price is inside stop and exit-z thresholds.
    strategy = make_strategy()
    bars = make_bars(STABLE, start=datetime(2026, 7, 1, 18, 15, tzinfo=UTC))
    signal = strategy.evaluate("AAPL", bars, make_quote(99.0), position=make_position())
    assert signal.action is SignalAction.SELL
    assert "end of day" in signal.reason


def test_end_of_day_flatten_works_with_short_history() -> None:
    strategy = make_strategy()
    bars = make_bars(STABLE[:8], start=datetime(2026, 7, 1, 19, 15, tzinfo=UTC))
    signal = strategy.evaluate("AAPL", bars, make_quote(99.0), position=make_position())
    assert signal.action is SignalAction.SELL
    assert "end of day" in signal.reason


def test_entry_blocked_inside_cutoff_window() -> None:
    # A dip that would otherwise buy, but the last bar is 15 minutes before
    # the close.
    strategy = make_strategy()
    bars = make_bars(DIP_IN_UPTREND, start=datetime(2026, 7, 1, 17, 20, tzinfo=UTC))
    signal = strategy.evaluate("AAPL", bars, make_quote(90.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "entry blocked" in signal.reason
    assert "entry cutoff" in signal.reason


def test_trend_filter_blocks_entry() -> None:
    strategy = make_strategy()
    bars = make_bars(DIP_IN_DOWNTREND)
    signal = strategy.evaluate("AAPL", bars, make_quote(90.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "below EMA" in signal.reason


def test_trend_filter_judges_mean_not_dipped_price() -> None:
    # The dipped price sits below the trend EMA (that is what a dip is), but
    # the pre-dip mean is above it, so the entry must not be blocked.
    strategy = make_strategy()
    bars = make_bars(DIP_IN_UPTREND)
    signal = strategy.evaluate("AAPL", bars, make_quote(90.0), position=None)
    assert signal.indicators["price"] < signal.indicators["trend_ema"]
    assert signal.indicators["mean"] > signal.indicators["trend_ema"]
    assert signal.action is SignalAction.BUY


def test_rsi_filter_blocks_entry() -> None:
    strategy = make_strategy(rsi_entry_max=1.0)
    bars = make_bars(DIP_IN_UPTREND)
    signal = strategy.evaluate("AAPL", bars, make_quote(90.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "RSI" in signal.reason


def test_all_filters_pass_buys() -> None:
    strategy = make_strategy(rsi_entry_max=35.0)
    bars = make_bars(DIP_IN_UPTREND)
    signal = strategy.evaluate("AAPL", bars, make_quote(90.0), position=None)
    assert signal.action is SignalAction.BUY
    assert "uptrend" in signal.reason
    assert {"zscore", "trend_ema", "rsi"} <= set(signal.indicators)


def test_insufficient_bars_for_trend_ema_blocks_entry() -> None:
    strategy = make_strategy(lookback=10)
    bars = make_bars([*STABLE[:-1], 80.0])
    signal = strategy.evaluate("AAPL", bars, make_quote(80.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "insufficient history for trend" in signal.reason


def test_required_bars_covers_all_periods() -> None:
    assert MeanReversionStrategy().required_bars().lookback == 205
    assert make_strategy().required_bars().lookback == 35


def test_required_bars_uses_configured_minute_timeframe() -> None:
    assert MeanReversionStrategy().required_bars().timeframe == Timeframe.minutes(5)
    assert MeanReversionStrategy(timeframe_minutes=15).required_bars().timeframe == (
        Timeframe.minutes(15)
    )


def test_registry_builds_with_params() -> None:
    strategy = build_strategy("mean_reversion", {"lookback": 30, "entry_z": -1.5})
    assert isinstance(strategy, MeanReversionStrategy)
    assert strategy.required_bars().lookback == 205


def test_registry_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="unknown strategy"):
        build_strategy("momentum", {})


def test_invalid_params_rejected() -> None:
    with pytest.raises(ValueError, match="lookback"):
        MeanReversionStrategy(lookback=1)
    with pytest.raises(ValueError, match="exit_z"):
        MeanReversionStrategy(entry_z=-1.0, exit_z=-2.0)
    with pytest.raises(ValueError, match="trend_ema_period"):
        MeanReversionStrategy(trend_ema_period=1)
    with pytest.raises(ValueError, match="rsi_period"):
        MeanReversionStrategy(rsi_period=1)
    with pytest.raises(ValueError, match="rsi_entry_max"):
        MeanReversionStrategy(rsi_entry_max=0.0)
    with pytest.raises(ValueError, match="atr_period"):
        MeanReversionStrategy(atr_period=0)
    with pytest.raises(ValueError, match="atr_stop_multiplier"):
        MeanReversionStrategy(atr_stop_multiplier=0.0)
