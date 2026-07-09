"""Feature-level tests on synthetic return series with known regime character."""

import numpy as np
import numpy.typing as npt
import pytest

from carcharoth.regime.features import (
    CusumFeature,
    HurstFeature,
    VolClusteringFeature,
    WassersteinFeature,
)

N = 512


def ar1_returns(phi: float, n: int = N, seed: int = 7) -> npt.NDArray[np.float64]:
    """AR(1) log returns: phi > 0 momentum (trending), phi < 0 mean reversion."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 0.01, n)
    returns = np.zeros(n)
    for i in range(1, n):
        returns[i] = phi * returns[i - 1] + noise[i]
    return returns


def iid_returns(n: int = N, sigma: float = 0.01, seed: int = 7) -> npt.NDArray[np.float64]:
    return np.asarray(np.random.default_rng(seed).normal(0.0, sigma, n), dtype=np.float64)


# --- Hurst ---


def test_hurst_momentum_series_reads_trending() -> None:
    evidence = HurstFeature().compute(ar1_returns(phi=0.5))
    assert evidence is not None
    assert evidence.direction is not None
    assert evidence.value > 0.55
    assert evidence.direction > 0


def test_hurst_mean_reverting_series_reads_mean_reverting() -> None:
    evidence = HurstFeature().compute(ar1_returns(phi=-0.5))
    assert evidence is not None
    assert evidence.direction is not None
    assert evidence.value < 0.45
    assert evidence.direction < 0


def test_hurst_orders_regimes_correctly() -> None:
    trending = HurstFeature().compute(ar1_returns(phi=0.5))
    random = HurstFeature().compute(iid_returns())
    reverting = HurstFeature().compute(ar1_returns(phi=-0.5))
    assert trending is not None and random is not None and reverting is not None
    assert trending.value > random.value > reverting.value


def test_hurst_returns_none_during_warmup() -> None:
    feature = HurstFeature()
    assert feature.compute(iid_returns(n=feature.min_returns() - 1)) is None
    assert feature.compute(iid_returns(n=feature.min_returns())) is not None


def test_hurst_constant_returns_do_not_crash() -> None:
    assert HurstFeature().compute(np.zeros(N)) is None


def test_hurst_is_non_directional_free() -> None:
    evidence = HurstFeature().compute(iid_returns())
    assert evidence is not None
    assert evidence.stability is None


# --- Volatility clustering ---


def clustered_vol_returns(n: int = N, seed: int = 7) -> npt.NDArray[np.float64]:
    """Alternating calm/volatile blocks — strong volatility clustering."""
    rng = np.random.default_rng(seed)
    sigmas = np.where((np.arange(n) // 20) % 2 == 0, 0.005, 0.03)
    return np.asarray(rng.normal(0.0, 1.0, n) * sigmas, dtype=np.float64)


def test_vol_clustering_detects_clustered_blocks() -> None:
    evidence = VolClusteringFeature().compute(clustered_vol_returns())
    assert evidence is not None
    assert evidence.direction is not None
    assert evidence.value > 0.1
    assert evidence.direction <= -0.3


def test_vol_clustering_low_for_iid() -> None:
    evidence = VolClusteringFeature().compute(iid_returns())
    assert evidence is not None
    assert evidence.value < 0.1


def test_vol_clustering_returns_none_during_warmup() -> None:
    assert VolClusteringFeature(window=150).compute(iid_returns(n=149)) is None


def test_vol_clustering_constant_returns_are_calm() -> None:
    evidence = VolClusteringFeature().compute(np.zeros(N))
    assert evidence is not None
    assert evidence.value == 0.0
    assert evidence.direction == 0.0


# --- CUSUM ---


def test_cusum_alarms_on_mean_shift() -> None:
    returns = iid_returns(n=150)
    returns[75:] += 1.5 * returns.std()
    evidence = CusumFeature(window=150).compute(returns)
    assert evidence is not None
    assert evidence.stability == 0.0


def test_cusum_stable_without_shift() -> None:
    evidence = CusumFeature(window=150).compute(iid_returns(n=150))
    assert evidence is not None
    assert evidence.stability is not None
    assert evidence.stability > 0.5


def test_cusum_flat_series_is_fully_stable() -> None:
    evidence = CusumFeature(window=150).compute(np.zeros(150))
    assert evidence is not None
    assert evidence.stability == 1.0


def test_cusum_returns_none_during_warmup() -> None:
    assert CusumFeature(window=150).compute(iid_returns(n=149)) is None


def test_cusum_is_non_directional() -> None:
    evidence = CusumFeature(window=150).compute(iid_returns(n=150))
    assert evidence is not None
    assert evidence.direction is None


# --- Wasserstein ---


def test_wasserstein_alarms_on_volatility_regime_shift() -> None:
    rng = np.random.default_rng(7)
    reference = rng.normal(0.0, 0.01, 180)
    recent = rng.normal(0.0, 0.03, 60)
    evidence = WassersteinFeature().compute(np.concatenate([reference, recent]))
    assert evidence is not None
    assert evidence.stability is not None
    assert evidence.stability < 0.5


def test_wasserstein_stable_for_unchanged_distribution() -> None:
    evidence = WassersteinFeature().compute(iid_returns(n=240))
    assert evidence is not None
    assert evidence.stability is not None
    assert evidence.stability > 0.7


def test_wasserstein_is_scale_invariant() -> None:
    returns = iid_returns(n=240, sigma=0.01)
    small = WassersteinFeature().compute(returns)
    large = WassersteinFeature().compute(returns * 100)
    assert small is not None and large is not None
    assert small.value == pytest.approx(large.value)


def test_wasserstein_returns_none_during_warmup() -> None:
    assert WassersteinFeature().compute(iid_returns(n=239)) is None


def test_wasserstein_flat_reference_is_stable() -> None:
    assert_flat = WassersteinFeature().compute(np.zeros(240))
    assert assert_flat is not None
    assert assert_flat.stability == 1.0
