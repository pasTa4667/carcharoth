from datetime import UTC, datetime

import pytest

from carcharoth.strategies.filters import AtrBracket, EndOfDayFilter, VolumeFilter
from tests.factories import make_bars

PRICES = [100.0] * 6


def test_passes_at_exact_ratio() -> None:
    result = VolumeFilter(period=5, min_ratio=1.0).check(
        make_bars(PRICES, volumes=[1000, 1000, 1000, 1000, 1000, 1000])
    )
    assert result.passed
    assert result.indicators["volume_ratio"] == pytest.approx(1.0)


def test_passes_above_ratio() -> None:
    result = VolumeFilter(period=5, min_ratio=1.2).check(
        make_bars(PRICES, volumes=[1000, 1000, 1000, 1000, 1000, 1500])
    )
    assert result.passed
    assert "1.50x" in result.reason


def test_fails_below_ratio() -> None:
    result = VolumeFilter(period=5, min_ratio=1.0).check(
        make_bars(PRICES, volumes=[1000, 1000, 1000, 1000, 1000, 500])
    )
    assert not result.passed
    assert "below required" in result.reason
    assert result.indicators["volume_ratio"] == pytest.approx(0.5)


def test_current_bar_excluded_from_average() -> None:
    # A huge current bar must not inflate its own benchmark: the average is
    # 1000 (previous 5 bars), so the ratio is 10, not ~2.9.
    result = VolumeFilter(period=5, min_ratio=1.0).check(
        make_bars(PRICES, volumes=[1000, 1000, 1000, 1000, 1000, 10000])
    )
    assert result.passed
    assert result.indicators["volume_sma"] == pytest.approx(1000.0)
    assert result.indicators["volume_ratio"] == pytest.approx(10.0)


def test_insufficient_history_fails() -> None:
    result = VolumeFilter(period=5).check(make_bars(PRICES[:5]))
    assert not result.passed
    assert "insufficient history" in result.reason
    assert result.indicators == {}


def test_zero_volume_history_fails() -> None:
    result = VolumeFilter(period=5).check(make_bars(PRICES, volumes=[0] * 6))
    assert not result.passed
    assert result.reason == "no usable volume history"


def test_invalid_params_rejected() -> None:
    with pytest.raises(ValueError, match="period"):
        VolumeFilter(period=0)
    with pytest.raises(ValueError, match="min_ratio"):
        VolumeFilter(min_ratio=0.0)


# --- AtrBracket ---

# Flat closes with a 2-point high-low range give ATR = 2, so a bracket with
# stop 1x / take-profit 2x around entry 100 sits at 98 / 104.
BRACKET_BARS = make_bars([100.0] * 10, hl_range=2.0)


def test_bracket_stop_loss_exits() -> None:
    result = AtrBracket(atr_period=5, stop_multiplier=1.0, take_profit_multiplier=2.0).check(
        BRACKET_BARS, entry_price=100.0, price=97.0
    )
    assert result.passed
    assert "stop loss" in result.reason
    assert result.indicators["stop_price"] == pytest.approx(98.0)


def test_bracket_take_profit_exits() -> None:
    result = AtrBracket(atr_period=5, stop_multiplier=1.0, take_profit_multiplier=2.0).check(
        BRACKET_BARS, entry_price=100.0, price=105.0
    )
    assert result.passed
    assert "take profit" in result.reason
    assert result.indicators["take_profit_price"] == pytest.approx(104.0)


def test_bracket_inside_holds() -> None:
    result = AtrBracket(atr_period=5, stop_multiplier=1.0, take_profit_multiplier=2.0).check(
        BRACKET_BARS, entry_price=100.0, price=100.0
    )
    assert not result.passed
    assert result.reason == "inside bracket"
    assert {"atr", "stop_price", "take_profit_price"} == set(result.indicators)


def test_bracket_without_take_profit_never_exits_upward() -> None:
    result = AtrBracket(atr_period=5, stop_multiplier=1.0, take_profit_multiplier=None).check(
        BRACKET_BARS, entry_price=100.0, price=200.0
    )
    assert not result.passed
    assert "take_profit_price" not in result.indicators


def test_bracket_insufficient_history_holds() -> None:
    result = AtrBracket(atr_period=5).check(BRACKET_BARS[:4], entry_price=100.0, price=50.0)
    assert not result.passed
    assert "insufficient history" in result.reason


def test_bracket_required_lookback() -> None:
    assert AtrBracket(atr_period=14).required_lookback() == 15


def test_bracket_invalid_params_rejected() -> None:
    with pytest.raises(ValueError, match="atr_period"):
        AtrBracket(atr_period=0)
    with pytest.raises(ValueError, match="stop_multiplier"):
        AtrBracket(stop_multiplier=0.0)
    with pytest.raises(ValueError, match="take_profit_multiplier"):
        AtrBracket(take_profit_multiplier=0.0)


# --- EndOfDayFilter ---

# 2026-07-01 is an EDT date: the 16:00 ET close is 20:00 UTC.
MID_SESSION = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)  # 11:00 ET
NEAR_CLOSE = datetime(2026, 7, 1, 19, 45, tzinfo=UTC)  # 15:45 ET
FLATTEN_TIME = datetime(2026, 7, 1, 19, 50, tzinfo=UTC)  # 15:50 ET
AFTER_CLOSE = datetime(2026, 7, 1, 20, 30, tzinfo=UTC)  # 16:30 ET


def test_eod_allows_entry_mid_session() -> None:
    result = EndOfDayFilter(entry_cutoff_minutes=30, flatten_minutes=15).blocks_entry(MID_SESSION)
    assert not result.passed
    assert result.indicators["minutes_until_close"] == pytest.approx(300.0)


def test_eod_blocks_entry_inside_cutoff() -> None:
    result = EndOfDayFilter(entry_cutoff_minutes=30, flatten_minutes=15).blocks_entry(NEAR_CLOSE)
    assert result.passed
    assert "entry cutoff" in result.reason


def test_eod_no_flatten_outside_window() -> None:
    result = EndOfDayFilter(entry_cutoff_minutes=30, flatten_minutes=15).should_flatten(
        datetime(2026, 7, 1, 19, 40, tzinfo=UTC)  # 15:40 ET, 20 min to close
    )
    assert not result.passed


def test_eod_flattens_inside_window() -> None:
    result = EndOfDayFilter(entry_cutoff_minutes=30, flatten_minutes=15).should_flatten(
        FLATTEN_TIME
    )
    assert result.passed
    assert "end of day" in result.reason


def test_eod_fires_after_close() -> None:
    eod = EndOfDayFilter(entry_cutoff_minutes=30, flatten_minutes=15)
    assert eod.blocks_entry(AFTER_CLOSE).passed
    assert eod.should_flatten(AFTER_CLOSE).passed


def test_eod_invalid_params_rejected() -> None:
    with pytest.raises(ValueError, match="flatten_minutes"):
        EndOfDayFilter(flatten_minutes=-1)
    with pytest.raises(ValueError, match="entry_cutoff_minutes"):
        EndOfDayFilter(entry_cutoff_minutes=5, flatten_minutes=15)
