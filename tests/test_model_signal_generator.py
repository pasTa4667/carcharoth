from datetime import UTC, date, datetime

from carcharoth.config.app_config import DecisionPolicyConfig, FeatureSpecConfig
from carcharoth.domain.models import (
    MarketSnapshot,
    ModelTask,
    Prediction,
    TargetExposure,
)
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.modeling.artifact import ArtifactManifest
from carcharoth.modeling.decision import DecisionPolicy
from carcharoth.modeling.signal_generator import XGBoostSignalGenerator
from tests.factories import make_bars, make_position


class ConstantPredictor:
    def __init__(self, value: float) -> None:
        self.value = value
        self.calls = 0

    def predict(self, features):  # type: ignore[no-untyped-def]
        self.calls += 1
        return Prediction(ModelTask.REGRESSION, expected_return=self.value)


def manifest(schema: str) -> ArtifactManifest:
    return ArtifactManifest(
        model_id="m1",
        model_sha256="hash",
        config_hash="config",
        schema_hash=schema,
        feature_names=["return_1"],
        feature_descriptors=[],
        task=ModelTask.REGRESSION,
        target={"type": "regression", "horizon_bars": 5},
        timeframe_minutes=5,
        benchmark_symbol="SPY",
        symbols=["AAPL"],
        xgboost_params={},
        seed=42,
        train_start=date(2024, 1, 1),
        train_end=date(2024, 6, 1),
        validation_start=date(2024, 6, 2),
        validation_end=date(2024, 12, 31),
        train_rows=10,
        validation_rows=5,
        created_at=datetime.now(UTC),
    )


def generator(value: float = 0.01):
    pipeline = FeaturePipeline(
        [FeatureSpecConfig(name="log_return", params={"period": 1})], 5, "SPY"
    )
    predictor = ConstantPredictor(value)
    return (
        XGBoostSignalGenerator(
            pipeline,
            predictor,
            manifest(pipeline.schema_hash),
            DecisionPolicy(DecisionPolicyConfig()),
        ),
        predictor,
    )


def test_one_prediction_per_completed_bar() -> None:
    subject, predictor = generator()
    bars = make_bars([100, 101])
    snapshot = MarketSnapshot({"AAPL": bars, "SPY": bars}, {}, bars[-1].timestamp)
    first = subject.generate("AAPL", snapshot, None)
    second = subject.generate("AAPL", snapshot, None)
    assert first.target is TargetExposure.LONG
    assert second.target is TargetExposure.UNCHANGED
    assert predictor.calls == 1


def test_missing_features_are_unchanged_not_flat() -> None:
    subject, _ = generator()
    bars = make_bars([100])
    snapshot = MarketSnapshot({"AAPL": bars, "SPY": bars}, {}, bars[-1].timestamp)
    assert subject.generate("AAPL", snapshot, make_position()).target is TargetExposure.UNCHANGED


def test_end_of_day_flatten_overrides_feature_warmup() -> None:
    subject, predictor = generator()
    timestamp = datetime(2026, 7, 1, 19, 50, tzinfo=UTC)
    bars = make_bars([100], start=timestamp)
    snapshot = MarketSnapshot({"AAPL": bars, "SPY": []}, {}, timestamp)
    intent = subject.generate("AAPL", snapshot, make_position())
    assert intent.target is TargetExposure.FLAT
    assert predictor.calls == 0


def test_end_of_day_flatten_works_even_when_symbol_bars_are_missing() -> None:
    subject, _ = generator()
    timestamp = datetime(2026, 7, 1, 19, 55, tzinfo=UTC)
    snapshot = MarketSnapshot({"AAPL": [], "SPY": []}, {}, timestamp)
    assert subject.generate("AAPL", snapshot, make_position()).target is TargetExposure.FLAT


def test_regression_hysteresis_holds_long_until_exit_threshold() -> None:
    policy = DecisionPolicy(DecisionPolicyConfig())
    target, _ = policy.decide(
        Prediction(ModelTask.REGRESSION, expected_return=0.0001),
        make_position(),
        60,
        120,
    )
    assert target is TargetExposure.LONG


def test_prepared_exact_lookup_reset_and_atomic_failure(monkeypatch) -> None:
    from datetime import timedelta

    import pytest

    subject, predictor = generator()
    bars = make_bars([100, 101, 102, 103])
    snapshot = MarketSnapshot({"AAPL": bars[:2]}, {}, bars[-1].timestamp)
    expected = subject.generate("AAPL", snapshot, None)
    subject.prepare_history(
        {"AAPL": bars}, symbols=["AAPL"], start=bars[2].timestamp, end_exclusive=bars[-1].timestamp
    )

    def fail(*args, **kwargs):
        raise AssertionError("rolling recomputation in prepared mode")

    monkeypatch.setattr(subject._pipeline, "compute", fail)
    actual = subject.generate("AAPL", snapshot, None)
    assert actual == expected  # predecessor timestamp, not snapshot.as_of
    assert predictor.calls == 2  # successful preparation reset de-duplication
    with pytest.raises(ValueError, match="duplicate"):
        subject.prepare_history(
            {"AAPL": bars + bars[:1]},
            symbols=["AAPL"],
            start=bars[0].timestamp,
            end_exclusive=bars[-1].timestamp,
        )
    assert subject.generate("AAPL", snapshot, None).reason == "bar already evaluated"
    missing = MarketSnapshot({"AAPL": bars}, {}, bars[-1].timestamp)
    assert subject.generate("AAPL", missing, None).target is TargetExposure.UNCHANGED
    subject.prepare_history(
        {},
        symbols=["AAPL"],
        start=bars[0].timestamp,
        end_exclusive=bars[-1].timestamp + timedelta(minutes=5),
    )
    assert subject.generate("AAPL", snapshot, None).target is TargetExposure.UNCHANGED


def test_repreparation_uses_transformed_inputs() -> None:
    from dataclasses import replace
    from datetime import timedelta

    subject, _ = generator()
    bars = make_bars([100, 101])

    def prepare(values):
        subject.prepare_history(
            {"AAPL": values},
            symbols=["AAPL"],
            start=bars[0].timestamp,
            end_exclusive=bars[-1].timestamp + timedelta(minutes=5),
        )

    snapshot = MarketSnapshot({"AAPL": bars}, {}, bars[-1].timestamp)
    prepare(bars)
    first = subject.generate("AAPL", snapshot, None)
    prepare([bars[0], replace(bars[1], close=102)])
    second = subject.generate("AAPL", snapshot, None)
    assert first.features != second.features


def test_preparation_counts_only_target_matrices_and_safety_still_flattens() -> None:
    from datetime import timedelta

    from carcharoth.performance import Monitor

    subject, predictor = generator()
    bars = make_bars([100, 101, 102])
    monitor = Monitor()
    with monitor.execution():
        subject.prepare_history(
            {"AAPL": bars, "SPY": bars},
            symbols=["AAPL"],
            start=bars[1].timestamp,
            end_exclusive=bars[-1].timestamp + timedelta(minutes=5),
        )
        subject.generate("AAPL", MarketSnapshot({"AAPL": bars}, {}, bars[-1].timestamp), None)
    assert monitor.counters["feature_matrices"] == 1
    assert monitor.counters["preparation_rows"] == 3
    assert monitor.counters["features"] == 1
    assert any(s.path == "feature_preparation" for s in monitor.snapshot().stages)
    close = datetime(2026, 7, 1, 19, 55, tzinfo=UTC)
    intent = subject.generate("AAPL", MarketSnapshot({}, {}, close), make_position())
    assert intent.target is TargetExposure.FLAT and predictor.calls == 1
