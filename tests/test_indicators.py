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
