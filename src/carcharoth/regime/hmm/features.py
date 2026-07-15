"""Observation matrix for the HMM detector: one row per bar.

Columns (in this order):
  0. log return       — winsorized close-to-close log return (drift signal)
  1. log volatility   — log of the rolling std of log returns (vol level;
                        the log makes the heavily right-skewed raw vol
                        near-Gaussian, which Gaussian emissions need)
  2. ema distance     — (close - EMA) / EMA, a slow-moving trend-level signal
  3. adx              — directionless trend strength (range vs trend axis)

Pure numpy/TA-Lib — no hand-rolled indicators. Leading rows are NaN during
the indicators' warm-up and are dropped by the caller via `drop_warmup`.
"""

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt
import pandas as pd
import talib

from carcharoth.domain.models import Bar

#: column indices of the observation matrix
COL_RETURN = 0
COL_VOLATILITY = 1
COL_EMA_DISTANCE = 2
COL_ADX = 3

FEATURE_NAMES = ("log_return", "volatility", "ema_distance", "adx")

_VOL_EPS = 1e-12


def warmup_rows(vol_window: int, ema_period: int, adx_period: int) -> int:
    """Leading rows of the matrix that are NaN while indicators warm up.

    The first row is always NaN (no return for the first bar); rolling vol
    needs `vol_window` returns; EMA needs `ema_period` bars; TA-Lib's ADX
    emits its first value at bar `2 * adx_period - 1`.
    """
    return max(vol_window + 1, ema_period, 2 * adx_period) + 1


def build_feature_matrix(
    bars: Sequence[Bar],
    vol_window: int,
    ema_period: int,
    adx_period: int,
    winsorize_sigma: float,
) -> npt.NDArray[np.float64] | None:
    """Full-length observation matrix (one row per bar, NaN during warm-up);
    None when the series is too short to hold any complete row."""
    if len(bars) <= warmup_rows(vol_window, ema_period, adx_period):
        return None

    closes = np.array([bar.close for bar in bars], dtype=np.float64)
    highs = np.array([bar.high for bar in bars], dtype=np.float64)
    lows = np.array([bar.low for bar in bars], dtype=np.float64)
    if np.any(closes <= 0):
        return None

    log_closes = np.log(closes)
    returns = np.full(len(bars), np.nan, dtype=np.float64)
    returns[1:] = _winsorize(np.diff(log_closes), winsorize_sigma)

    matrix = np.full((len(bars), len(FEATURE_NAMES)), np.nan, dtype=np.float64)
    matrix[:, COL_RETURN] = returns
    matrix[:, COL_VOLATILITY] = _log_rolling_std(returns, vol_window)
    ema = np.asarray(talib.EMA(closes, timeperiod=ema_period), dtype=np.float64)
    matrix[:, COL_EMA_DISTANCE] = (closes - ema) / ema
    matrix[:, COL_ADX] = np.asarray(
        talib.ADX(highs, lows, closes, timeperiod=adx_period), dtype=np.float64
    )
    return matrix


def drop_warmup(matrix: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """The matrix without any row containing NaN (indicator warm-up)."""
    return matrix[~np.isnan(matrix).any(axis=1)]


def _winsorize(returns: npt.NDArray[np.float64], sigma: float) -> npt.NDArray[np.float64]:
    """Clip returns at +/- sigma std devs; blunts overnight gaps that would
    otherwise dominate multi-session intraday windows (same treatment as the
    score detector)."""
    std = float(np.std(returns))
    if std <= 0:
        return returns
    limit = sigma * std
    return np.clip(returns, -limit, limit)


def _log_rolling_std(returns: npt.NDArray[np.float64], window: int) -> npt.NDArray[np.float64]:
    """log(rolling std of returns), NaN while fewer than `window` returns
    exist. Pandas (not TA-Lib) because the input carries a leading NaN,
    which TA-Lib does not support."""
    values = pd.Series(returns).rolling(window).std().to_numpy(dtype=np.float64)
    return np.log(values + _VOL_EPS)
