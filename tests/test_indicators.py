import math

import pytest

from carcharoth.strategies import indicators
from tests.factories import make_bars

RISING = [100.0 + i for i in range(20)]
FALLING = [100.0 - i for i in range(20)]


def test_ema_needs_period_bars() -> None:
    bars = make_bars(RISING[:4])
    assert indicators.ema(bars, period=5) is None
    assert indicators.ema(make_bars(RISING[:5]), period=5) is not None


def test_rsi_needs_period_plus_one_bars() -> None:
    assert indicators.rsi(make_bars(RISING[:5]), period=5) is None
    assert indicators.rsi(make_bars(RISING[:6]), period=5) is not None


def test_atr_needs_period_plus_one_bars() -> None:
    assert indicators.atr(make_bars(RISING[:5]), period=5) is None
    assert indicators.atr(make_bars(RISING[:6]), period=5) is not None


def test_ema_of_constant_series_is_the_constant() -> None:
    bars = make_bars([100.0] * 10)
    assert indicators.ema(bars, period=5) == pytest.approx(100.0)


def test_atr_reflects_bar_range() -> None:
    bars = make_bars([100.0] * 10, hl_range=2.0)
    assert indicators.atr(bars, period=5) == pytest.approx(2.0)


def test_rsi_extremes() -> None:
    rising_rsi = indicators.rsi(make_bars(RISING), period=5)
    falling_rsi = indicators.rsi(make_bars(FALLING), period=5)
    assert rising_rsi is not None and rising_rsi > 70
    assert falling_rsi is not None and falling_rsi < 30


def test_volume_sma_needs_period_bars() -> None:
    assert indicators.volume_sma(make_bars(RISING[:4]), period=5) is None
    assert indicators.volume_sma(make_bars(RISING[:5]), period=5) is not None


def test_volume_sma_of_constant_volume_is_the_constant() -> None:
    bars = make_bars([100.0] * 10)  # factory default volume is 1000
    assert indicators.volume_sma(bars, period=5) == pytest.approx(1000.0)


def test_volume_sma_averages_the_last_period_bars() -> None:
    bars = make_bars([100.0] * 6, volumes=[9000, 9000, 1000, 2000, 3000, 4000])
    assert indicators.volume_sma(bars, period=4) == pytest.approx(2500.0)


def test_highest_high_needs_period_bars() -> None:
    assert indicators.highest_high(make_bars(RISING[:4]), period=5) is None
    assert indicators.highest_high(make_bars(RISING[:5]), period=5) is not None


def test_highest_high_ignores_bars_outside_window() -> None:
    prices = [200.0, 100.0, 101.0, 102.0, 103.0]
    bars = make_bars(prices, hl_range=2.0)
    assert indicators.highest_high(bars, period=4) == pytest.approx(104.0)


def test_ema_series_needs_period_bars() -> None:
    assert indicators.ema_series(make_bars(RISING[:4]), period=5) is None
    assert indicators.ema_series(make_bars(RISING[:5]), period=5) is not None


def test_ema_series_tail_matches_ema() -> None:
    bars = make_bars(RISING)
    series = indicators.ema_series(bars, period=5)
    assert series is not None
    assert float(series[-1]) == pytest.approx(indicators.ema(bars, period=5))


def test_ema_series_is_nan_during_warmup() -> None:
    series = indicators.ema_series(make_bars(RISING), period=5)
    assert series is not None
    assert math.isnan(series[3])
    assert not math.isnan(series[4])


def test_vwap_weights_by_volume() -> None:
    bars = make_bars([100.0, 102.0], volumes=[1000, 3000])
    assert indicators.vwap(bars) == pytest.approx(101.5)


def test_vwap_uses_typical_price() -> None:
    # high 103, low 97, close 100 -> typical price (103 + 97 + 100) / 3 = 100
    bars = make_bars([100.0], hl_range=6.0)
    assert indicators.vwap(bars) == pytest.approx(100.0)


def test_vwap_empty_or_zero_volume_is_none() -> None:
    assert indicators.vwap([]) is None
    assert indicators.vwap(make_bars([100.0] * 3, volumes=[0, 0, 0])) is None
