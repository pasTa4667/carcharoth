"""HMM fit cache: config/observation key sensitivity, round-trip fidelity,
and cached-fit assessments being identical to fresh ones."""

from datetime import UTC, datetime

import numpy as np
import pytest
from hmmlearn.hmm import GaussianHMM

from carcharoth.regime.hmm.detector import HmmRegimeDetector, SymbolModel
from carcharoth.regime.hmm.fit_cache import HmmFitCache, hmm_config_hash
from carcharoth.regime.models import Regime
from tests.factories import make_bars
from tests.fakes import InMemoryByteStore

FITTED_AT = datetime(2026, 6, 1, 15, 0, tzinfo=UTC)


def small_observations(seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).normal(size=(60, 2))


def small_model(observations: np.ndarray) -> SymbolModel:
    model = GaussianHMM(n_components=4, covariance_type="diag", n_iter=10, random_state=1)
    model.fit(observations)
    return SymbolModel(
        model=model,
        scaler_mean=observations.mean(axis=0),
        scaler_std=observations.std(axis=0),
        labels={
            0: Regime.TRENDING_UP,
            1: Regime.TRENDING_DOWN,
            2: Regime.RANGE_BOUND,
            3: Regime.HIGH_VOLATILITY,
        },
        fitted_through=FITTED_AT,
    )


def test_config_hash_is_canonical_and_sensitive() -> None:
    assert hmm_config_hash({"n_states": 4, "seed": 42}) == hmm_config_hash(
        {"seed": 42, "n_states": 4}  # field order doesn't matter
    )
    assert hmm_config_hash({"n_states": 4, "seed": 42}) != hmm_config_hash(
        {"n_states": 5, "seed": 42}
    )


def test_round_trip_restores_fit_with_callers_fitted_through() -> None:
    store = InMemoryByteStore()
    cache = HmmFitCache(store, "cfg")
    observations = small_observations()
    original = small_model(observations)

    cache.store("AAPL", observations, original)
    later = datetime(2026, 6, 2, 15, 0, tzinfo=UTC)
    loaded = cache.load("AAPL", observations, fitted_through=later)

    assert loaded is not None
    assert loaded.fitted_through == later  # never the stored horizon
    assert np.array_equal(loaded.model.transmat_, original.model.transmat_)
    assert np.array_equal(loaded.model.means_, original.model.means_)
    assert np.array_equal(loaded.scaler_mean, original.scaler_mean)
    assert dict(loaded.labels) == dict(original.labels)


def test_different_config_symbol_or_observations_miss() -> None:
    store = InMemoryByteStore()
    observations = small_observations()
    HmmFitCache(store, "cfg-a").store("AAPL", observations, small_model(observations))

    assert HmmFitCache(store, "cfg-b").load("AAPL", observations, FITTED_AT) is None
    assert HmmFitCache(store, "cfg-a").load("MSFT", observations, FITTED_AT) is None
    other = small_observations(seed=1)
    assert HmmFitCache(store, "cfg-a").load("AAPL", other, FITTED_AT) is None
    assert HmmFitCache(store, "cfg-a").load("AAPL", observations, FITTED_AT) is not None


def test_corrupt_payload_is_a_miss() -> None:
    store = InMemoryByteStore()
    cache = HmmFitCache(store, "cfg")
    observations = small_observations()
    cache.store("AAPL", observations, small_model(observations))
    key = next(iter(store.data))
    store.data[key] = b"not a pickle"
    assert cache.load("AAPL", observations, FITTED_AT) is None


def synthetic_prices() -> list[float]:
    """Four regime-like segments so a 4-state fit isn't degenerate."""
    rng = np.random.default_rng(7)
    segments = [
        rng.normal(0.002, 0.002, 160),  # up
        rng.normal(-0.002, 0.002, 100),  # down
        rng.normal(0.0, 0.0005, 100),  # calm
        rng.normal(0.0, 0.012, 100),  # wild
    ]
    return list(100.0 * np.exp(np.cumsum(np.concatenate(segments))))


def make_detector(store: InMemoryByteStore) -> HmmRegimeDetector:
    return HmmRegimeDetector(
        training_window=400,
        vol_window=10,
        ema_period=20,
        adx_period=10,
        seed=42,
        fit_cache=HmmFitCache(store, "cfg"),
    )


def test_cached_fit_reproduces_fresh_assessment_without_fitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryByteStore()
    bars = make_bars(synthetic_prices(), hl_range=0.3)

    first = make_detector(store).assess("AAPL", bars)
    assert first is not None
    assert store.count_prefix("carch:hmm:") == 1

    fits: list[str] = []
    monkeypatch.setattr(
        HmmRegimeDetector, "_fit", lambda self, symbol, *a, **k: fits.append(symbol)
    )
    second = make_detector(store).assess("AAPL", bars)  # fresh detector, warm store
    assert fits == []  # served entirely from the cache
    assert second is not None
    assert second.regime is first.regime
    assert second.probabilities == first.probabilities
