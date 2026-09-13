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


# Fixed before candidate acceptance comparisons. Do not widen after observing drift.
RTOL, ATOL = 1e-10, 1e-12


@pytest.mark.parametrize(
    "name",
    [
        "log_return",
        "ema_distance",
        "adx",
        "normalized_atr",
        "normalized_rsi",
        "realized_volatility",
        "range_position",
        "benchmark_return",
        "session_vwap_distance",
        "time_of_day_volume_zscore",
        "time_of_day",
    ],
)
def test_prepared_batch_equivalence_and_future_causality(name) -> None:
    from dataclasses import replace

    import numpy as np

    params = (
        {"component": "sin"}
        if name == "time_of_day"
        else {"sessions": 3}
        if name == "time_of_day_volume_zscore"
        else {}
        if name == "session_vwap_distance"
        else {"period": 3}
    )
    pipeline = FeaturePipeline([spec(name, **params)], 5, "SPY")
    bars = [
        bar
        for day in range(8)
        for bar in make_bars(
            [100 + day + np.sin(i) for i in range(30)],
            hl_range=2,
            start=datetime(2026, 3, 5 + day, 15, tzinfo=UTC),
            volumes=[100 + day + i for i in range(30)],
        )
    ]
    benchmark = [replace(b, symbol="SPY") for i, b in enumerate(bars) if i % 7]
    ordered, matrix = pipeline.compute_matrix(bars, benchmark)
    cutoff = ordered[150].timestamp
    prefix = [b for b in bars if b.timestamp <= cutoff]
    benchmark_prefix = [b for b in benchmark if b.timestamp <= cutoff]
    _, expected = pipeline.compute_matrix(prefix, benchmark_prefix)
    for future in ([], bars[151:], [replace(b, close=b.close * 2, volume=0) for b in bars[151:]]):
        _, actual = pipeline.compute_matrix(prefix + future, benchmark)
        np.testing.assert_array_equal(np.isfinite(actual[:151]), np.isfinite(expected))
        np.testing.assert_allclose(actual[:151], expected, rtol=RTOL, atol=ATOL, equal_nan=True)
    changed_benchmark = [replace(b, close=-1) if b.timestamp > cutoff else b for b in benchmark]
    _, actual = pipeline.compute_matrix(bars, changed_benchmark)
    np.testing.assert_allclose(actual[:151], expected, rtol=RTOL, atol=ATOL, equal_nan=True)
    table = pipeline.prepare_history(
        list(reversed(bars)), benchmark, start=cutoff, end_exclusive=ordered[180].timestamp
    )
    assert len(table.rows) == 31  # 30 execution rows + latest predecessor
    assert not table.matrix.flags.writeable and table.matrix.base is None
    for i in range(149, 180):
        vector = table.lookup(ordered[i].timestamp)
        assert (vector is not None) == bool(np.isfinite(matrix[i]).all())
        if vector is not None:
            assert vector.names == pipeline.feature_names
            assert vector.schema_hash == pipeline.schema_hash
            assert vector.as_of == ordered[i].timestamp
            np.testing.assert_allclose(vector.values, matrix[i], rtol=RTOL, atol=ATOL)
    assert table.lookup(ordered[148].timestamp) is None


def test_custom_scalar_fallback_cannot_see_future_benchmark() -> None:
    from carcharoth.domain.models import Timeframe
    from carcharoth.features.base import Feature

    class Custom(Feature):
        output_name = "custom"

        def required_bars(self, timeframe):
            return 1

        def compute(self, context):
            assert all(b.timestamp <= context.as_of for b in context.benchmark_bars)
            return float(len(context.benchmark_bars))

    bars = make_bars([100, 101, 102])
    assert Custom().compute_series(bars[:2], tuple(bars), Timeframe.minutes(5)).tolist() == [1, 2]


def test_predecessor_unavailable_is_not_replaced_by_older_finite_row() -> None:
    pipeline = FeaturePipeline([spec("log_return", period=1)], 5, "SPY")
    bars = make_bars([100, 101, 0, 103])
    table = pipeline.prepare_history(
        bars, [], start=bars[-1].timestamp, end_exclusive=bars[-1].timestamp + timedelta(minutes=5)
    )
    assert set(table.rows) == {b.timestamp for b in bars[-2:]}
    assert table.lookup(bars[-2].timestamp) is None
    with pytest.raises(ValueError, match="duplicate"):
        pipeline.prepare_history(
            bars + bars[:1], [], start=bars[0].timestamp, end_exclusive=bars[-1].timestamp
        )


def test_recursive_legacy_snapshot_drift_is_not_batch_identity() -> None:
    import numpy as np

    from carcharoth.services.backtest.market_data import HistoricalMarketDataService

    pipeline = FeaturePipeline([spec("ema_distance", period=3)], 5, "SPY")
    bars = make_bars([100 + np.sin(i) for i in range(50)])
    market = HistoricalMarketDataService({"AAPL": bars}, spread_pct=0)
    market.advance_to(bars[-1].timestamp)
    snapshot = market.get_snapshot(["AAPL"], pipeline.required_bars())
    legacy = pipeline.compute(snapshot.bars["AAPL"], [])
    batch = pipeline.compute(bars, [])
    assert legacy is not None and batch is not None
    # This is a semantic audit: recursive initialization exceeds the fixed gate.
    assert not np.allclose(legacy.values, batch.values, rtol=RTOL, atol=ATOL)
