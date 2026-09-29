from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import numpy as np
import pytest

from carcharoth.domain.models import ModelTask
from carcharoth.modeling.dataset import DatasetRow, ModelDataset
from carcharoth.modeling.diagnostics import dataset_diagnostics, regression_diagnostics


def dataset(
    actual: list[float], feature_names: tuple[str, ...] = ("signal", "noise")
) -> ModelDataset:
    rows = tuple(
        DatasetRow("AAPL", datetime(2024, 1, 2, tzinfo=UTC) + timedelta(minutes=index))
        for index in range(len(actual))
    )
    return ModelDataset(
        features=np.zeros((len(actual), len(feature_names)), dtype=np.float64),
        labels=np.asarray(actual, dtype=np.float64),
        feature_names=feature_names,
        rows=rows,
        task=ModelTask.REGRESSION,
        attempted_rows=len(actual),
    )


@pytest.mark.parametrize("with_constant", [False, True])
def test_dataset_correlations_handle_two_varying_features(with_constant):
    names = ("a", "b", "constant") if with_constant else ("a", "b")
    values = np.asarray([[1, 2, 0], [2, 4, 0], [3, 6, 0]], dtype=float)[:, : len(names)]
    result = dataset_diagnostics(replace(dataset([1, 2, 3], names), features=values))
    assert result["constant_features"] == (["constant"] if with_constant else [])
    assert result["high_correlations"] == [{"left": "a", "right": "b", "correlation": 1.0}]


def test_deciles_include_every_row_once_and_preserve_rank_order() -> None:
    actual = [float(value % 3 - 1) for value in range(23)]
    predictions = np.asarray([0.0] * 4 + [float(value) for value in range(19)])

    result = regression_diagnostics(dataset(actual), predictions, {}, {})
    deciles = result["prediction_deciles"]

    assert len(deciles) == 10
    assert sum(bucket["count"] for bucket in deciles) == 23
    assert all(
        left["prediction_max"] <= right["prediction_min"] for left, right in pairwise(deciles)
    )


def test_small_constant_dataset_has_finite_histogram_and_one_bucket_per_row() -> None:
    result = regression_diagnostics(
        dataset([0.5, 0.5, 0.5]),
        np.asarray([0.5, 0.5, 0.5]),
        {},
        {},
        distribution_bins=4,
    )

    assert len(result["prediction_deciles"]) == 3
    assert [bucket["count"] for bucket in result["prediction_deciles"]] == [1, 1, 1]
    bins = result["distributions"]["bins"]
    assert sum(item["prediction_count"] for item in bins) == 3
    assert sum(item["actual_count"] for item in bins) == 3
    assert all(np.isfinite(item["lower"]) and np.isfinite(item["upper"]) for item in bins)


def test_scatter_sampling_is_deterministic_bounded_and_keeps_extremes() -> None:
    predictions = np.linspace(-1.0, 1.0, num=101)
    validation = dataset(predictions.tolist())

    first = regression_diagnostics(validation, predictions, {}, {}, scatter_limit=11)
    second = regression_diagnostics(validation, predictions, {}, {}, scatter_limit=11)
    points = first["prediction_vs_actual"]["scatter"]

    assert points == second["prediction_vs_actual"]["scatter"]
    assert len(points) == 11
    assert points[0]["prediction"] == -1.0
    assert points[-1]["prediction"] == 1.0
    assert points[0]["symbol"] == "AAPL"
    assert "timestamp" in points[0]


def test_feature_importance_follows_schema_and_includes_unused_features() -> None:
    result = regression_diagnostics(
        dataset([0.0, 1.0]),
        np.asarray([0.1, 0.9]),
        {"signal": 4.0},
        {"signal": 0.2, "noise": -0.01},
    )

    importance = result["feature_importance"]
    assert [item["feature"] for item in importance] == ["signal", "noise"]
    assert importance[0]["gain_normalized"] == 1.0
    assert importance[1]["gain"] == 0.0
    assert importance[1]["permutation_rmse_increase"] == -0.01


def test_rejects_misaligned_or_non_finite_predictions() -> None:
    validation = dataset([0.0, 1.0])
    with pytest.raises(ValueError, match="align"):
        regression_diagnostics(validation, np.asarray([0.0]), {}, {})
    with pytest.raises(ValueError, match="finite"):
        regression_diagnostics(validation, np.asarray([0.0, np.nan]), {}, {})
