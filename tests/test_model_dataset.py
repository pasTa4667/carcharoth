import math
from datetime import UTC, datetime, timedelta

import pytest

from carcharoth.config.app_config import (
    ClassificationTargetConfig,
    FeatureSpecConfig,
    RegressionTargetConfig,
)
from carcharoth.domain.models import Bar, ModelTask
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.modeling.dataset import CLASS_LONG, build_dataset

START = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)


def bars(symbol: str, prices: list[float], start: datetime = START) -> list[Bar]:
    return [
        Bar(
            symbol,
            start + timedelta(minutes=5 * index),
            price,
            price,
            price,
            price,
            1000,
        )
        for index, price in enumerate(prices)
    ]


def pipeline() -> FeaturePipeline:
    return FeaturePipeline([FeatureSpecConfig(name="log_return", params={"period": 1})], 5, "SPY")


def test_regression_label_is_next_open_to_horizon_close() -> None:
    values = bars("AAPL", [100, 101, 102, 105, 110, 120])
    data = {"AAPL": values, "SPY": bars("SPY", [100] * 6)}
    dataset = build_dataset(
        data,
        ["AAPL"],
        pipeline(),
        RegressionTargetConfig(horizon_bars=2),
        START,
        START + timedelta(hours=1),
    )
    assert dataset.task is ModelTask.REGRESSION
    assert dataset.rows[0].timestamp == values[1].timestamp
    assert dataset.labels[0] == pytest.approx(math.log(values[3].close / values[2].open))


def test_classification_uses_configured_neutral_band() -> None:
    data = {"AAPL": bars("AAPL", [100, 100, 101, 103]), "SPY": bars("SPY", [100] * 4)}
    dataset = build_dataset(
        data,
        ["AAPL"],
        pipeline(),
        ClassificationTargetConfig(horizon_bars=2, neutral_band=0.001),
        START,
        START + timedelta(hours=1),
    )
    assert dataset.task is ModelTask.CLASSIFICATION
    assert CLASS_LONG in dataset.labels


def test_targets_do_not_cross_session_or_window_end() -> None:
    first = bars("AAPL", [100, 101], START)
    second_start = START + timedelta(days=1)
    values = first + bars("AAPL", [200, 201, 202], second_start)
    benchmark = bars("SPY", [100, 100], START) + bars("SPY", [100, 100, 100], second_start)
    dataset = build_dataset(
        {"AAPL": values, "SPY": benchmark},
        ["AAPL"],
        pipeline(),
        RegressionTargetConfig(horizon_bars=2),
        START,
        second_start,
    )
    assert dataset.complete_rows == 0
