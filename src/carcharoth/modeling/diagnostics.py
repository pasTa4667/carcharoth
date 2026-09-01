"""Dataset diagnostics persisted with every model artifact."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.stats import spearmanr

from carcharoth.domain.models import ModelTask
from carcharoth.modeling.dataset import ModelDataset


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
    if matrix.shape[0] >= 2 and matrix.shape[1] >= 2:
        correlation = np.asarray(spearmanr(matrix, axis=0).statistic, dtype=np.float64)
        for left in range(matrix.shape[1]):
            for right in range(left + 1, matrix.shape[1]):
                value = float(correlation[left, right])
                if np.isfinite(value) and abs(value) >= correlation_threshold:
                    high_correlations.append(
                        {
                            "left": dataset.feature_names[left],
                            "right": dataset.feature_names[right],
                            "correlation": value,
                        }
                    )
    result: dict[str, Any] = {
        "attempted_rows": dataset.attempted_rows,
        "complete_rows": dataset.complete_rows,
        "dropped_rows": dataset.attempted_rows - dataset.complete_rows,
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
