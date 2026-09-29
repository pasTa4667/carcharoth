"""Dataset and post-training diagnostics persisted with model artifacts."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.stats import spearmanr

from carcharoth.domain.models import ModelTask
from carcharoth.modeling.dataset import ModelDataset

DIAGNOSTICS_SCHEMA_VERSION = 1
SCATTER_SAMPLE_LIMIT = 5_000
DISTRIBUTION_BIN_COUNT = 30


def dataset_diagnostics(
    dataset: ModelDataset, correlation_threshold: float = 0.95
) -> dict[str, Any]:
    matrix = dataset.features
    constants = [
        name
        for index, name in enumerate(dataset.feature_names)
        if matrix.shape[0] == 0 or float(np.ptp(matrix[:, index])) == 0.0
    ]
    high_correlations: list[dict[str, float | str]] = []
    varying = [i for i, name in enumerate(dataset.feature_names) if name not in constants]
    if matrix.shape[0] >= 2 and len(varying) >= 2:
        correlation = np.asarray(spearmanr(matrix[:, varying], axis=0).statistic, dtype=np.float64)
        if correlation.ndim == 0:  # scipy returns a scalar for exactly two columns.
            value = float(correlation)
            correlation = np.asarray([[1.0, value], [value, 1.0]])
        for left in range(len(varying)):
            for right in range(left + 1, len(varying)):
                value = float(correlation[left, right])
                if np.isfinite(value) and abs(value) >= correlation_threshold:
                    high_correlations.append(
                        {
                            "left": dataset.feature_names[varying[left]],
                            "right": dataset.feature_names[varying[right]],
                            "correlation": value,
                        }
                    )
    result: dict[str, Any] = {
        "attempted_rows": dataset.attempted_rows,
        "complete_rows": dataset.complete_rows,
        "dropped_rows": dataset.attempted_rows - dataset.complete_rows,
        "rows_by_symbol": {
            symbol: {
                **evidence.model_dump(mode="json"),
                "usable_coverage": evidence.usable_coverage,
                "raw_coverage": evidence.raw_coverage,
            }
            for symbol, evidence in dataset.evidence.items()
        },
        "constant_features": constants,
        "high_correlations": high_correlations,
    }
    if dataset.task is ModelTask.CLASSIFICATION:
        values, counts = np.unique(dataset.labels, return_counts=True)
        result["class_balance"] = {
            str(int(value)): int(count) for value, count in zip(values, counts, strict=True)
        }
    else:
        labels = dataset.labels.astype(np.float64)
        result["target"] = {
            "mean": float(np.mean(labels)) if labels.size else None,
            "std": float(np.std(labels)) if labels.size else None,
            "min": float(np.min(labels)) if labels.size else None,
            "max": float(np.max(labels)) if labels.size else None,
        }
    return result


def regression_diagnostics(
    validation: ModelDataset,
    predictions: np.ndarray,
    gain_importance: dict[str, float],
    permutation_importance: dict[str, float],
    *,
    scatter_limit: int = SCATTER_SAMPLE_LIMIT,
    distribution_bins: int = DISTRIBUTION_BIN_COUNT,
) -> dict[str, Any]:
    """Return bounded, JSON-safe diagnostics for one regression validation set.

    All aggregate views use every validation row. Only ``scatter`` is bounded;
    it is sampled at evenly spaced prediction ranks and therefore retains both
    extremes. Feature output always follows the configured feature order.
    """
    if validation.task is not ModelTask.REGRESSION:
        raise ValueError("regression diagnostics require a regression dataset")
    actual = validation.labels.astype(np.float64)
    predicted = np.asarray(predictions, dtype=np.float64).reshape(-1)
    if actual.shape != predicted.shape:
        raise ValueError("validation labels and predictions must align")
    if actual.size == 0:
        raise ValueError("regression diagnostics require validation rows")
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ValueError("regression diagnostics require finite labels and predictions")
    if validation.rows and len(validation.rows) != actual.size:
        raise ValueError("validation row metadata must align with labels")
    if scatter_limit < 2:
        raise ValueError("scatter_limit must be at least 2")
    if distribution_bins < 1:
        raise ValueError("distribution_bins must be positive")

    return {
        "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
        "scope": "validation",
        "prediction_vs_actual": {
            "total_rows": int(actual.size),
            "sampled_rows": min(int(actual.size), scatter_limit),
            "scatter": _scatter(validation, predicted, actual, scatter_limit),
        },
        "prediction_deciles": _prediction_deciles(predicted, actual),
        "distributions": _distributions(predicted, actual, distribution_bins),
        "feature_importance": _feature_importance(
            validation.feature_names, gain_importance, permutation_importance
        ),
    }


def _summary(values: np.ndarray) -> dict[str, float | int]:
    quantiles = np.quantile(values, [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    return {
        "count": int(values.size),
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "min": float(np.min(values)),
        "p01": float(quantiles[0]),
        "p05": float(quantiles[1]),
        "p25": float(quantiles[2]),
        "median": float(quantiles[3]),
        "p75": float(quantiles[4]),
        "p95": float(quantiles[5]),
        "p99": float(quantiles[6]),
        "max": float(np.max(values)),
    }


def _scatter(
    dataset: ModelDataset,
    predicted: np.ndarray,
    actual: np.ndarray,
    limit: int,
) -> list[dict[str, float | str]]:
    order = np.argsort(predicted, kind="stable")
    if order.size > limit:
        positions = np.linspace(0, order.size - 1, num=limit, dtype=np.int64)
        order = order[positions]
    points: list[dict[str, float | str]] = []
    for index in order:
        point: dict[str, float | str] = {
            "prediction": float(predicted[index]),
            "actual": float(actual[index]),
        }
        if dataset.rows:
            row = dataset.rows[int(index)]
            point.update(symbol=row.symbol, timestamp=row.timestamp.isoformat())
        points.append(point)
    return points


def _prediction_deciles(predicted: np.ndarray, actual: np.ndarray) -> list[dict[str, float | int]]:
    order = np.argsort(predicted, kind="stable")
    bucket_count = min(10, int(order.size))
    buckets: list[dict[str, float | int]] = []
    for bucket, indices in enumerate(np.array_split(order, bucket_count), start=1):
        bucket_prediction = predicted[indices]
        bucket_actual = actual[indices]
        error = bucket_prediction - bucket_actual
        buckets.append(
            {
                "decile": bucket,
                "count": int(indices.size),
                "prediction_min": float(np.min(bucket_prediction)),
                "prediction_max": float(np.max(bucket_prediction)),
                "prediction_mean": float(np.mean(bucket_prediction)),
                "actual_mean": float(np.mean(bucket_actual)),
                "actual_median": float(np.median(bucket_actual)),
                "actual_std": float(np.std(bucket_actual)),
                "positive_actual_rate": float(np.mean(bucket_actual > 0)),
                "mae": float(np.mean(np.abs(error))),
                "rmse": float(math.sqrt(float(np.mean(error**2)))),
            }
        )
    return buckets


def _distributions(predicted: np.ndarray, actual: np.ndarray, bin_count: int) -> dict[str, Any]:
    lower = float(min(np.min(predicted), np.min(actual)))
    upper = float(max(np.max(predicted), np.max(actual)))
    if lower == upper:
        padding = max(abs(lower) * 0.01, 1e-12)
        lower -= padding
        upper += padding
    edges = np.linspace(lower, upper, num=bin_count + 1)
    prediction_counts, _ = np.histogram(predicted, bins=edges)
    actual_counts, _ = np.histogram(actual, bins=edges)
    bins = [
        {
            "lower": float(edges[index]),
            "upper": float(edges[index + 1]),
            "midpoint": float((edges[index] + edges[index + 1]) / 2),
            "prediction_count": int(prediction_counts[index]),
            "actual_count": int(actual_counts[index]),
        }
        for index in range(bin_count)
    ]
    return {
        "prediction": _summary(predicted),
        "actual": _summary(actual),
        "bins": bins,
    }


def _feature_importance(
    feature_names: tuple[str, ...],
    gain_importance: dict[str, float],
    permutation_importance: dict[str, float],
) -> list[dict[str, float | str]]:
    gains = np.asarray([gain_importance.get(name, 0.0) for name in feature_names])
    total_gain = float(np.sum(gains))
    return [
        {
            "feature": name,
            "gain": float(gains[index]),
            "gain_normalized": float(gains[index] / total_gain) if total_gain > 0 else 0.0,
            "permutation_rmse_increase": float(permutation_importance.get(name, 0.0)),
        }
        for index, name in enumerate(feature_names)
    ]
