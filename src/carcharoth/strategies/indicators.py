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


def _closes(bars: Sequence[Bar]) -> npt.NDArray[np.float64]:
    return np.array([bar.close for bar in bars], dtype=np.float64)


def _latest(values: npt.NDArray[np.float64]) -> float | None:
    last = float(values[-1])
    return None if math.isnan(last) else last
