from typing import Protocol

from carcharoth.domain.models import FeatureVector, Prediction


class Predictor(Protocol):
    def predict(self, features: FeatureVector) -> Prediction: ...
