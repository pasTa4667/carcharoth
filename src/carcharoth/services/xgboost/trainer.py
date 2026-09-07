"""Native XGBoost training for regression and three-class classification."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import scipy.stats
import xgboost as xgb

from carcharoth.domain.models import ModelTask
from carcharoth.modeling.dataset import ModelDataset

_OWNED_PARAMS = frozenset({"objective", "eval_metric", "seed", "num_class"})


@dataclass(frozen=True, slots=True)
class TrainingResult:
    booster: xgb.Booster
    metrics: dict[str, float]
    feature_importance: dict[str, float]
    permutation_importance: dict[str, float]
    validation_predictions: np.ndarray
    best_iteration: int

    def save_model(self, path: Path) -> None:
        self.booster.save_model(path)


def train_model(
    train: ModelDataset,
    validation: ModelDataset,
    params: dict[str, bool | int | float | str],
    seed: int,
    early_stopping_rounds: int,
) -> TrainingResult:
    if train.complete_rows == 0 or validation.complete_rows == 0:
        raise ValueError("training and validation datasets must both contain complete rows")
    if train.task is not validation.task:
        raise ValueError("training and validation tasks differ")
    if train.feature_names != validation.feature_names:
        raise ValueError("training and validation feature schemas differ")
    forbidden = _OWNED_PARAMS & params.keys()
    if forbidden:
        raise ValueError(f"xgboost params owned by the adapter: {sorted(forbidden)}")

    effective: dict[str, Any] = {
        "tree_method": "hist",
        "nthread": 1,
        **params,
        "seed": seed,
    }
    rounds = int(effective.pop("num_boost_round", 500))
    if train.task is ModelTask.REGRESSION:
        effective.update(objective="reg:squarederror", eval_metric="rmse")
    else:
        effective.update(objective="multi:softprob", eval_metric="mlogloss", num_class=3)

    dtrain = xgb.DMatrix(
        train.features,
        label=train.labels,
        feature_names=list(train.feature_names),
    )
    dvalidation = xgb.DMatrix(
        validation.features,
        label=validation.labels,
        feature_names=list(validation.feature_names),
    )
    booster = xgb.train(
        effective,
        dtrain,
        num_boost_round=rounds,
        evals=[(dtrain, "train"), (dvalidation, "validation")],
        early_stopping_rounds=early_stopping_rounds,
        verbose_eval=False,
    )
    prediction = np.asarray(booster.predict(dvalidation), dtype=np.float64)
    metrics = _metrics(validation, prediction)
    importance = {
        name: float(np.mean(value)) if isinstance(value, list) else float(value)
        for name, value in booster.get_score(importance_type="gain").items()
    }
    permutation_importance = (
        _permutation_importance(booster, validation, prediction.reshape(-1), seed)
        if validation.task is ModelTask.REGRESSION
        else {}
    )
    return TrainingResult(
        booster=booster,
        metrics=metrics,
        feature_importance=importance,
        permutation_importance=permutation_importance,
        validation_predictions=prediction,
        best_iteration=int(booster.best_iteration),
    )


def _permutation_importance(
    booster: xgb.Booster,
    validation: ModelDataset,
    baseline_prediction: np.ndarray,
    seed: int,
) -> dict[str, float]:
    """Measure validation RMSE degradation after independently shuffling each feature."""
    actual = validation.labels.astype(np.float64)
    baseline_rmse = math.sqrt(float(np.mean((baseline_prediction - actual) ** 2)))
    rng = np.random.default_rng(seed)
    matrix = validation.features.copy()
    importance: dict[str, float] = {}
    for index, name in enumerate(validation.feature_names):
        original = matrix[:, index].copy()
        matrix[:, index] = rng.permutation(original)
        permuted = np.asarray(
            booster.predict(xgb.DMatrix(matrix, feature_names=list(validation.feature_names))),
            dtype=np.float64,
        ).reshape(-1)
        permuted_rmse = math.sqrt(float(np.mean((permuted - actual) ** 2)))
        importance[name] = permuted_rmse - baseline_rmse
        matrix[:, index] = original
    return importance


def _metrics(dataset: ModelDataset, prediction: np.ndarray) -> dict[str, float]:
    if dataset.task is ModelTask.REGRESSION:
        actual = dataset.labels.astype(np.float64)
        error = prediction.reshape(-1) - actual
        correlation = scipy.stats.spearmanr(actual, prediction.reshape(-1)).statistic
        return {
            "rmse": float(math.sqrt(float(np.mean(error**2)))),
            "mae": float(np.mean(np.abs(error))),
            "spearman": float(correlation) if np.isfinite(correlation) else 0.0,
        }

    probabilities = prediction.reshape((-1, 3))
    actual_int = dataset.labels.astype(np.int64)
    selected = np.clip(probabilities[np.arange(actual_int.size), actual_int], 1e-15, 1.0)
    predicted = np.argmax(probabilities, axis=1)
    recalls = []
    for class_id in range(3):
        mask = actual_int == class_id
        if np.any(mask):
            recalls.append(float(np.mean(predicted[mask] == class_id)))
    return {
        "log_loss": float(-np.mean(np.log(selected))),
        "accuracy": float(np.mean(predicted == actual_int)),
        "balanced_accuracy": float(np.mean(recalls)) if recalls else 0.0,
    }
