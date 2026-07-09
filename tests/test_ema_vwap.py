from datetime import UTC, datetime
from typing import Any

import pytest

from carcharoth.domain.models import SignalAction, Timeframe
from carcharoth.strategies.ema_vwap import EmaVwapStrategy
from carcharoth.strategies.registry import build_strategy
from tests.factories import make_bars, make_position, make_quote

# A decline followed by a sharp two-bar rally: the fast EMA crosses above the
# slow EMA on the final bar, so an entry evaluated here sees a fresh cross.
FRESH_CROSS = [100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0, 91.0, 90.0, 96.0, 100.0]

# A steady rise: the fast EMA has been above the slow EMA for many bars, so
# any cross is stale by the final bar.
STEADY_RISE = [90.0 + i for i in range(13)]

FLAT = [100.0] * 13

# Bars ending at 15:50 / 15:45 ET (10 / 15 min before the close); factory
# bars are 5 minutes apart, so start = end - (len - 1) * 5 min.
FLATTEN_WINDOW_START = datetime(2026, 7, 1, 18, 50, tzinfo=UTC)
ENTRY_CUTOFF_START = datetime(2026, 7, 1, 18, 45, tzinfo=UTC)


def make_strategy(**overrides: Any) -> EmaVwapStrategy:
    """Strategy with EMA/ATR periods small enough for compact test fixtures."""
    params: dict[str, Any] = {
        "ema_fast": 3,
        "ema_slow": 8,
        "cross_within_bars": 2,
        "atr_period": 5,
        "atr_stop_multiplier": 1.0,
        "atr_take_profit_multiplier": 2.0,
    }
    params.update(overrides)
    return EmaVwapStrategy(**params)


def test_fresh_cross_above_vwap_buys() -> None:
    strategy = make_strategy()
    signal = strategy.evaluate("AAPL", make_bars(FRESH_CROSS), make_quote(100.0), position=None)
    assert signal.action is SignalAction.BUY
    assert "fresh cross" in signal.reason
    assert signal.indicators["ema_fast"] > signal.indicators["ema_slow"]
    assert signal.indicators["price"] > signal.indicators["vwap"]


def test_price_below_vwap_blocks_entry() -> None:
    strategy = make_strategy()
    signal = strategy.evaluate("AAPL", make_bars(FRESH_CROSS), make_quote(90.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "not above VWAP" in signal.reason


def test_stale_cross_blocks_entry() -> None:
    strategy = make_strategy()
    signal = strategy.evaluate("AAPL", make_bars(STEADY_RISE), make_quote(103.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "stale cross" in signal.reason


def test_fast_ema_below_slow_blocks_entry() -> None:
    # One bar before the cross completes: the rally has started but the fast
    # EMA is still below the slow one.
    strategy = make_strategy()
    signal = strategy.evaluate(
        "AAPL", make_bars(FRESH_CROSS[:-1]), make_quote(100.0), position=None
    )
    assert signal.action is SignalAction.HOLD
    assert "no uptrend" in signal.reason


def test_entry_blocked_inside_cutoff_window() -> None:
    strategy = make_strategy()
    bars = make_bars(FRESH_CROSS, start=ENTRY_CUTOFF_START)
    signal = strategy.evaluate("AAPL", bars, make_quote(100.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "entry blocked" in signal.reason
    assert "entry cutoff" in signal.reason


def test_take_profit_sells() -> None:
    strategy = make_strategy()
    bars = make_bars(FLAT, hl_range=2.0)  # ATR ~ 2, take profit ~ 104
    signal = strategy.evaluate("AAPL", bars, make_quote(105.0), position=make_position())
    assert signal.action is SignalAction.SELL
    assert "take profit" in signal.reason
    assert signal.indicators["take_profit_price"] < 105.0


def test_stop_loss_sells() -> None:
    strategy = make_strategy()
    bars = make_bars(FLAT, hl_range=2.0)  # ATR ~ 2, stop ~ 98
    signal = strategy.evaluate("AAPL", bars, make_quote(97.0), position=make_position())
    assert signal.action is SignalAction.SELL
    assert "stop loss" in signal.reason
    assert signal.indicators["stop_price"] > 97.0


def test_trend_reversal_sells_inside_bracket() -> None:
    strategy = make_strategy()
    bars = make_bars(list(reversed(STEADY_RISE)))  # falling: fast EMA below slow
    signal = strategy.evaluate("AAPL", bars, make_quote(89.5), position=make_position(price=90.0))
    assert signal.action is SignalAction.SELL
    assert "trend reversed" in signal.reason


def test_stop_loss_beats_trend_reversal() -> None:
    strategy = make_strategy()
    bars = make_bars(list(reversed(STEADY_RISE)))
    signal = strategy.evaluate("AAPL", bars, make_quote(80.0), position=make_position(price=90.0))
    assert signal.action is SignalAction.SELL
    assert "stop loss" in signal.reason


def test_end_of_day_flattens_position() -> None:
    strategy = make_strategy()
    bars = make_bars(FLAT, hl_range=2.0, start=FLATTEN_WINDOW_START)
    signal = strategy.evaluate("AAPL", bars, make_quote(100.0), position=make_position())
    assert signal.action is SignalAction.SELL
    assert "end of day" in signal.reason


def test_uptrend_inside_bracket_holds() -> None:
    strategy = make_strategy()
    bars = make_bars(STEADY_RISE)  # ATR ~ 1: bracket around entry 100 is ~99..102
    signal = strategy.evaluate("AAPL", bars, make_quote(101.0), position=make_position())
    assert signal.action is SignalAction.HOLD
    assert "uptrend intact" in signal.reason


def test_missing_emas_with_position_holds() -> None:
    # Enough bars for the ATR bracket but not the slow EMA: never sell on
    # missing data, only the bracket and flatten exits are available.
    strategy = make_strategy()
    bars = make_bars(FLAT[:7], hl_range=2.0)
    signal = strategy.evaluate("AAPL", bars, make_quote(100.0), position=make_position())
    assert signal.action is SignalAction.HOLD
    assert "insufficient history" in signal.reason


def test_short_history_blocks_entry() -> None:
    strategy = make_strategy()
    signal = strategy.evaluate("AAPL", make_bars(FLAT[:3]), make_quote(101.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "insufficient history" in signal.reason


def test_no_bars_holds() -> None:
    strategy = make_strategy()
    signal = strategy.evaluate("AAPL", [], make_quote(100.0), position=None)
    assert signal.action is SignalAction.HOLD
    assert "insufficient history" in signal.reason


def test_missing_quote_falls_back_to_last_close() -> None:
    strategy = make_strategy()
    signal = strategy.evaluate("AAPL", make_bars(FRESH_CROSS), quote=None, position=None)
    assert signal.action is SignalAction.BUY
    assert signal.indicators["price"] == 100.0


def test_signal_carries_strategy_name() -> None:
    strategy = make_strategy()
    signal = strategy.evaluate("AAPL", make_bars(FRESH_CROSS), make_quote(100.0), position=None)
    assert signal.strategy == "ema_vwap"


def test_required_bars_covers_full_session() -> None:
    # 5-minute default: a 390-minute session dominates the EMA warm-up.
    assert EmaVwapStrategy().required_bars().lookback == 83
    assert EmaVwapStrategy(timeframe_minutes=1).required_bars().lookback == 395


def test_required_bars_uses_configured_minute_timeframe() -> None:
    assert EmaVwapStrategy().required_bars().timeframe == Timeframe.minutes(5)
    assert EmaVwapStrategy(timeframe_minutes=1).required_bars().timeframe == Timeframe.minutes(1)


def test_registry_builds_with_params() -> None:
    strategy = build_strategy("ema_vwap", {"ema_fast": 5, "ema_slow": 13})
    assert isinstance(strategy, EmaVwapStrategy)


def test_invalid_params_rejected() -> None:
    with pytest.raises(ValueError, match="ema_fast"):
        EmaVwapStrategy(ema_fast=1)
    with pytest.raises(ValueError, match="ema_slow"):
        EmaVwapStrategy(ema_fast=9, ema_slow=9)
    with pytest.raises(ValueError, match="cross_within_bars"):
        EmaVwapStrategy(cross_within_bars=0)
    with pytest.raises(ValueError, match="atr_period"):
        EmaVwapStrategy(atr_period=0)
    with pytest.raises(ValueError, match="atr_stop_multiplier"):
        EmaVwapStrategy(atr_stop_multiplier=0.0)
    with pytest.raises(ValueError, match="atr_take_profit_multiplier"):
        EmaVwapStrategy(atr_take_profit_multiplier=0.0)
    with pytest.raises(ValueError, match="timeframe_minutes"):
        EmaVwapStrategy(timeframe_minutes=0)
    with pytest.raises(ValueError, match="entry_cutoff_minutes"):
        EmaVwapStrategy(entry_cutoff_minutes=5, flatten_minutes=15)
