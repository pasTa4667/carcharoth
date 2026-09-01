from datetime import UTC, datetime, timedelta

import pytest

from carcharoth.config.app_config import FeatureSpecConfig
from carcharoth.domain.models import Bar
from carcharoth.features.pipeline import FeaturePipeline
from tests.factories import make_bars


def spec(name: str, **params: object) -> FeatureSpecConfig:
    return FeatureSpecConfig(name=name, params=params)


def test_order_and_params_define_schema_and_values() -> None:
    bars = make_bars([100, 101, 102, 104, 108])
    pipeline = FeaturePipeline(
        [spec("log_return", period=1), spec("log_return", period=4)], 5, "SPY"
    )
    vector = pipeline.compute(bars, bars)
    assert vector is not None
    assert vector.names == ("return_1", "return_4")
    assert vector.values[0] == pytest.approx(0.0377403, rel=1e-4)
    assert vector.values[1] == pytest.approx(0.076961, rel=1e-4)

    changed = FeaturePipeline([spec("log_return", period=2)], 5, "SPY")
    assert changed.schema_hash != pipeline.schema_hash


def test_duplicate_output_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        FeaturePipeline([spec("log_return", period=1), spec("log_return", period=1)], 5, "SPY")


def test_missing_benchmark_returns_no_vector() -> None:
    pipeline = FeaturePipeline([spec("benchmark_return", period=1)], 5, "SPY")
    assert pipeline.compute(make_bars([100, 101]), []) is None


def test_flat_range_has_neutral_position() -> None:
    pipeline = FeaturePipeline([spec("range_position", period=3)], 5, "SPY")
    bars = make_bars([100, 100, 100])
    vector = pipeline.compute(bars, bars)
    assert vector is not None
    assert vector.values == (0.5,)


def test_volume_zscore_uses_same_bar_of_day_across_sessions() -> None:
    start = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)
    bars = [
        Bar("AAPL", start + timedelta(days=day), 1, 1, 1, 1, float(100 + day)) for day in range(21)
    ]
    pipeline = FeaturePipeline([spec("time_of_day_volume_zscore", sessions=20)], 5, "SPY")
    vector = pipeline.compute(bars, bars)
    assert vector is not None
    assert vector.values[0] > 0
