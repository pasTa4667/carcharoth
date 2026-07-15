"""Observation-matrix construction for the HMM detector."""

import numpy as np
import pytest

from carcharoth.regime.hmm.features import (
    COL_ADX,
    COL_EMA_DISTANCE,
    COL_RETURN,
    COL_VOLATILITY,
    FEATURE_NAMES,
    build_feature_matrix,
    drop_warmup,
    warmup_rows,
)
from tests.factories import make_bars

PARAMS = {"vol_window": 10, "ema_period": 20, "adx_period": 10, "winsorize_sigma": 5.0}


def make_matrix(prices: list[float]) -> np.ndarray:
    matrix = build_feature_matrix(make_bars(prices, hl_range=0.5), **PARAMS)
    assert matrix is not None
    return matrix


def random_walk(n: int, drift: float = 0.0, sigma: float = 0.005, seed: int = 3) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(100.0 * np.exp(np.cumsum(rng.normal(drift, sigma, n))))


def test_matrix_shape_and_warmup_nan_rows() -> None:
    n = 120
    matrix = make_matrix(random_walk(n))
    assert matrix.shape == (n, len(FEATURE_NAMES))
    clean = drop_warmup(matrix)
    # warmup_rows is a conservative bound on the NaN prefix
    assert n - warmup_rows(10, 20, 10) <= len(clean) <= n
    assert not np.isnan(clean).any()


def test_too_short_series_returns_none() -> None:
    prices = random_walk(warmup_rows(10, 20, 10))
    assert build_feature_matrix(make_bars(prices, hl_range=0.5), **PARAMS) is None


def test_nonpositive_prices_return_none() -> None:
    prices = random_walk(120)
    prices[50] = 0.0
    assert build_feature_matrix(make_bars(prices, hl_range=0.5), **PARAMS) is None


def test_trending_prices_have_positive_ema_distance_tail() -> None:
    matrix = make_matrix(random_walk(200, drift=0.003, sigma=0.001))
    clean = drop_warmup(matrix)
    assert np.all(clean[-20:, COL_EMA_DISTANCE] > 0)
    assert np.mean(clean[-20:, COL_RETURN]) > 0


def test_volatile_prices_have_higher_vol_column() -> None:
    calm = drop_warmup(make_matrix(random_walk(200, sigma=0.001)))
    wild = drop_warmup(make_matrix(random_walk(200, sigma=0.02)))
    assert wild[-1, COL_VOLATILITY] > calm[-1, COL_VOLATILITY]


def test_extreme_gap_is_winsorized() -> None:
    prices = random_walk(200, sigma=0.002)
    prices = [*prices, prices[-1] * 1.5, *random_walk(30, sigma=0.002, seed=4)]
    matrix = build_feature_matrix(make_bars(prices, hl_range=0.5), **PARAMS)
    assert matrix is not None
    returns = matrix[1:, COL_RETURN]
    # the +50% gap return would be ~0.4; the clip keeps it near 5 sigma
    assert np.nanmax(returns) < 0.4
    assert np.nanmax(returns) == pytest.approx(np.nanmax(np.abs(returns)))


def test_adx_column_is_bounded() -> None:
    clean = drop_warmup(make_matrix(random_walk(200)))
    assert np.all(clean[:, COL_ADX] >= 0)
    assert np.all(clean[:, COL_ADX] <= 100)
