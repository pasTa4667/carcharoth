"""Thin, typed wrappers around TA-Lib taking domain Bars.

Each returns the latest indicator value, or None when the series is too
short to compute one (TA-Lib emits NaN during warm-up).
"""

import math
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt
import talib

from carcharoth.domain.models import Bar


def ema(bars: Sequence[Bar], period: int) -> float | None:
    """Exponential moving average of closes; first value after `period` bars."""
    if len(bars) < period:
        return None
    values = talib.EMA(_closes(bars), timeperiod=period)
    return _latest(values)


def ema_series(bars: Sequence[Bar], period: int) -> npt.NDArray[np.float64] | None:
    """Full EMA series of closes, one value per bar (NaN during warm-up);
    None when the series is too short to hold any value."""
    if len(bars) < period:
        return None
    return talib.EMA(_closes(bars), timeperiod=period)


def vwap(bars: Sequence[Bar]) -> float | None:
    """Volume-weighted average of typical price ((H+L+C)/3) over the given
    bars. Callers anchor the window (e.g. pass the current session's bars);
    None on empty input or zero total volume."""
    if not bars:
        return None
    volumes = np.array([bar.volume for bar in bars], dtype=np.float64)
    total_volume = float(volumes.sum())
    if total_volume <= 0:
        return None
    typical = np.array([(bar.high + bar.low + bar.close) / 3 for bar in bars], dtype=np.float64)
    return float(np.dot(typical, volumes) / total_volume)


def rsi(bars: Sequence[Bar], period: int) -> float | None:
    """Relative strength index of closes; first value after `period + 1` bars."""
    if len(bars) < period + 1:
        return None
    values = talib.RSI(_closes(bars), timeperiod=period)
    return _latest(values)


def atr(bars: Sequence[Bar], period: int) -> float | None:
    """Average true range; first value after `period + 1` bars."""
    if len(bars) < period + 1:
        return None
    highs = np.array([bar.high for bar in bars], dtype=np.float64)
    lows = np.array([bar.low for bar in bars], dtype=np.float64)
    values = talib.ATR(highs, lows, _closes(bars), timeperiod=period)
    return _latest(values)


def volume_sma(bars: Sequence[Bar], period: int) -> float | None:
    """Simple moving average of volume; first value after `period` bars."""
    if len(bars) < period:
        return None
    volumes = np.array([bar.volume for bar in bars], dtype=np.float64)
    values = talib.SMA(volumes, timeperiod=period)
    return _latest(values)


def highest_high(bars: Sequence[Bar], period: int) -> float | None:
    """Highest high of the last `period` bars."""
    if len(bars) < period:
        return None
    return max(bar.high for bar in bars[-period:])


def _closes(bars: Sequence[Bar]) -> npt.NDArray[np.float64]:
    return np.array([bar.close for bar in bars], dtype=np.float64)


def _latest(values: npt.NDArray[np.float64]) -> float | None:
    last = float(values[-1])
    return None if math.isnan(last) else last
