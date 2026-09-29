from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np

from carcharoth.config.app_config import FeatureSpecConfig, RegressionTargetConfig
from carcharoth.domain.models import Bar
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.features.session import TradingSession
from carcharoth.modeling.dataset import dataset_from_prepared, prepare_dataset


def fixture():
    start = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
    bars = [
        Bar("A", start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i, 100)
        for i in range(12)
    ]
    pipeline = FeaturePipeline(
        [FeatureSpecConfig(name="log_return", params={"period": 1})], 5, "SPY"
    )
    sessions = [TradingSession(start, start + timedelta(hours=1))]
    return start, bars, pipeline, sessions


def test_prepare_once_and_row_reasons_reconcile():
    start, bars, pipeline, sessions = fixture()
    with patch.object(pipeline, "compute_matrix", wraps=pipeline.compute_matrix) as compute:
        prepared = prepare_dataset({"A": bars}, ["A"], pipeline, sessions)
        train = dataset_from_prepared(
            prepared,
            ["A"],
            pipeline,
            RegressionTargetConfig(horizon_bars=2),
            start,
            start + timedelta(minutes=30),
            sessions,
        )
        val = dataset_from_prepared(
            prepared,
            ["A"],
            pipeline,
            RegressionTargetConfig(horizon_bars=2),
            start + timedelta(minutes=30),
            start + timedelta(hours=1),
            sessions,
        )
    assert compute.call_count == 1
    for data in (train, val):
        stats = data.evidence["A"]
        assert stats.usable_rows + sum(stats.rejections.values()) == stats.attempted_rows
        assert stats.expected_rows == 4
    assert train.complete_rows == 3
    assert val.complete_rows == 4
    assert train.select_symbols([]).features.shape == (0, 1)


def test_gap_invalid_and_duplicate_inputs():
    start, bars, pipeline, sessions = fixture()
    data = [
        *bars[:3],
        *bars[4:],
        bars[0],
        replace(bars[1], close=100.5),
        replace(bars[2], close=float("nan")),
    ]
    prepared = prepare_dataset({"A": data, "EMPTY": []}, ["A", "EMPTY"], pipeline, sessions)
    dataset = dataset_from_prepared(
        prepared,
        ["A", "EMPTY"],
        pipeline,
        RegressionTargetConfig(horizon_bars=2),
        start,
        start + timedelta(hours=1),
        sessions,
    )
    stats = dataset.evidence["A"]
    assert stats.input_errors == {"conflicting_duplicate": 1, "invalid_ohlcv": 1}
    assert stats.rejections["gapped_horizon"] == 2
    assert stats.raw_bars == 11
    assert dataset.evidence["EMPTY"].missing_sessions == 1
    assert np.isfinite(dataset.labels).all()
