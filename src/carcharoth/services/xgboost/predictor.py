"""Loaded native XGBoost model adapter."""

from pathlib import Path

import numpy as np
import xgboost as xgb

from carcharoth.domain.models import (
    FeatureVector,
    ModelTask,
    Prediction,
    TargetExposure,
)


class XGBoostPredictor:
    def __init__(
        self,
        model_path: Path,
        task: ModelTask,
        feature_names: tuple[str, ...],
    ) -> None:
        self._task = task
        self._feature_names = feature_names
        self._booster = xgb.Booster()
        self._booster.load_model(model_path)

    def predict(self, features: FeatureVector) -> Prediction:
        if features.names != self._feature_names:
            raise ValueError("prediction feature order does not match loaded model")
        matrix = xgb.DMatrix(
            np.asarray([features.values], dtype=np.float64),
            feature_names=list(features.names),
        )
        values = np.asarray(self._booster.predict(matrix), dtype=np.float64)
        if self._task is ModelTask.REGRESSION:
            return Prediction(task=self._task, expected_return=float(values.reshape(-1)[0]))
        probabilities = values.reshape((-1, 3))[0]
        return Prediction(
            task=self._task,
            probabilities={
                TargetExposure.SHORT: float(probabilities[0]),
                TargetExposure.FLAT: float(probabilities[1]),
                TargetExposure.LONG: float(probabilities[2]),
            },
        )
