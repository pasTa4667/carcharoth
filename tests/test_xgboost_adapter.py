from datetime import UTC, datetime

import numpy as np
import pytest

from carcharoth.domain.models import FeatureVector, ModelTask
from carcharoth.modeling.dataset import ModelDataset
from carcharoth.services.xgboost import XGBoostPredictor, train_model


def dataset(task: ModelTask, offset: int = 0) -> ModelDataset:
    rng = np.random.default_rng(42 + offset)
    features = rng.normal(size=(120, 2))
    if task is ModelTask.REGRESSION:
        labels = (features[:, 0] * 0.01 - features[:, 1] * 0.005).astype(np.float64)
    else:
        score = features[:, 0]
        labels = np.where(score < -0.4, 0, np.where(score > 0.4, 2, 1)).astype(np.int64)
    return ModelDataset(
        features=features,
        labels=labels,
        feature_names=("a", "b"),
        rows=(),
        task=task,
        attempted_rows=120,
    )


def test_regression_trains_saves_and_predicts(tmp_path) -> None:
    train = dataset(ModelTask.REGRESSION)
    validation = dataset(ModelTask.REGRESSION, 1)
    result = train_model(
        train,
        validation,
        {"num_boost_round": 30, "max_depth": 2},
        seed=1,
        early_stopping_rounds=5,
    )
    path = tmp_path / "model.ubj"
    result.save_model(path)
    predictor = XGBoostPredictor(path, ModelTask.REGRESSION, ("a", "b"))
    prediction = predictor.predict(
        FeatureVector(("a", "b"), (1.0, 0.0), "schema", datetime.now(UTC))
    )
    assert prediction.expected_return is not None
    assert result.metrics["rmse"] >= 0


def test_classification_emits_three_probabilities(tmp_path) -> None:
    train = dataset(ModelTask.CLASSIFICATION)
    validation = dataset(ModelTask.CLASSIFICATION, 1)
    result = train_model(
        train,
        validation,
        {"num_boost_round": 30, "max_depth": 2},
        seed=1,
        early_stopping_rounds=5,
    )
    path = tmp_path / "model.ubj"
    result.save_model(path)
    prediction = XGBoostPredictor(path, ModelTask.CLASSIFICATION, ("a", "b")).predict(
        FeatureVector(("a", "b"), (1.0, 0.0), "schema", datetime.now(UTC))
    )
    assert len(prediction.probabilities) == 3
    assert sum(prediction.probabilities.values()) == pytest.approx(1.0)
