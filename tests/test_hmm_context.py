from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from carcharoth.config.app_config import FeatureSpecConfig, ModelConfig, ModelHmmConfig
from carcharoth.domain.models import Bar
from carcharoth.features.pipeline import FeaturePipeline, build_feature_pipeline
from carcharoth.regime.hmm.context import HMM_CONTEXT_NAMES, HmmModelContext
from carcharoth.regime.models import Regime, RegimeAssessment
from tests.factories import make_bars


class DictStore:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.gets = 0
        self.sets = 0

    def get(self, key: str) -> bytes | None:
        self.gets += 1
        return self.values.get(key)

    def set(self, key: str, value: bytes) -> None:
        self.sets += 1
        self.values[key] = value

    def delete_prefix(self, prefix: str) -> int:
        keys = [key for key in self.values if key.startswith(prefix)]
        for key in keys:
            del self.values[key]
        return len(keys)

    def scan_prefix(self, prefix: str) -> Sequence[tuple[str, int]]:
        return [(key, len(value)) for key, value in self.values.items() if key.startswith(prefix)]


class FakeDetector:
    def __init__(self) -> None:
        self.calls = 0

    def required_lookback(self) -> int:
        return 3

    def assess(self, symbol: str, bars: Sequence[Bar]) -> RegimeAssessment | None:
        self.calls += 1
        if len(bars) < 3:
            return None
        up = min(0.7, len(bars) / 10)
        probabilities = {
            Regime.TRENDING_UP: up,
            Regime.TRENDING_DOWN: 0.1,
            Regime.RANGE_BOUND: 0.8 - up,
            Regime.HIGH_VOLATILITY: 0.1,
        }
        return RegimeAssessment(
            symbol,
            max(probabilities, key=probabilities.__getitem__),
            max(probabilities.values()),
            0.0,
            0.9,
            (),
            probabilities,
        )


def config() -> ModelHmmConfig:
    return ModelHmmConfig(
        training_window=3,
        vol_window=2,
        ema_period=2,
        adx_period=2,
        evaluate_interval_minutes=10,
        n_restarts=1,
    )


def test_context_columns_are_causal_scheduled_probabilities(monkeypatch) -> None:
    context = HmmModelContext(config(), 5)
    detector = FakeDetector()
    monkeypatch.setattr(context, "_build_detector", lambda store: detector)
    bars = make_bars([100, 101, 102, 103, 104, 105])

    short = context.compute_series("AAPL", bars[:5])
    long = context.compute_series("AAPL", bars)

    assert HMM_CONTEXT_NAMES == (
        "hmm_trending_up",
        "hmm_trending_down",
        "hmm_range_bound",
        "hmm_high_volatility",
    )
    assert np.isnan(short[:2]).all()
    assert np.allclose(short[2:].sum(axis=1), 1.0)
    assert np.allclose(short, long[:5], equal_nan=True)
    assert np.allclose(short[2], short[3])  # carried between 10-minute evaluations


def test_exact_history_cache_skips_detector_and_changed_input_misses(monkeypatch) -> None:
    store = DictStore()
    bars = make_bars([100, 101, 102, 103, 104])
    cold = HmmModelContext(config(), 5, store)
    detector = FakeDetector()
    monkeypatch.setattr(cold, "_build_detector", lambda cache: detector)
    expected = cold.compute_series("AAPL", bars)
    assert detector.calls
    assert store.sets == 1

    warm = HmmModelContext(config(), 5, store)
    monkeypatch.setattr(
        warm,
        "_build_detector",
        lambda cache: (_ for _ in ()).throw(AssertionError("cache should bypass detector")),
    )
    assert np.allclose(warm.compute_series("AAPL", bars), expected, equal_nan=True)

    changed = HmmModelContext(config(), 5, store)
    changed_detector = FakeDetector()
    monkeypatch.setattr(changed, "_build_detector", lambda cache: changed_detector)
    changed.compute_series("AAPL", make_bars([100, 101, 102, 103, 106]))
    assert changed_detector.calls


def test_disabled_pipeline_preserves_legacy_schema_and_avoids_store() -> None:
    spec = FeatureSpecConfig(name="log_return", params={"period": 1})
    legacy = FeaturePipeline([spec], 5, "SPY")
    model = ModelConfig(features=[spec], hmm=ModelHmmConfig(enabled=False))
    store = DictStore()
    disabled = build_feature_pipeline(model, store)

    assert disabled.feature_names == legacy.feature_names
    assert disabled.schema_hash == legacy.schema_hash
    assert disabled.required_bars() == legacy.required_bars()
    assert store.gets == store.sets == 0
