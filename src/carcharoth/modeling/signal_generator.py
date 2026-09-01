"""Deep runtime module: market history -> model-driven target exposure."""

from __future__ import annotations

import logging
from datetime import date, datetime

from carcharoth.config.app_config import ModelConfig
from carcharoth.domain.models import (
    ExposureIntent,
    MarketDataRequirements,
    MarketSnapshot,
    Position,
    TargetExposure,
)
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.features.session import minutes_until_close
from carcharoth.interfaces.model_signal import ModelSignalGenerator
from carcharoth.modeling.artifact import ArtifactManifest, validate_artifact
from carcharoth.modeling.decision import DecisionPolicy
from carcharoth.modeling.predictor import Predictor

logger = logging.getLogger(__name__)


class XGBoostSignalGenerator(ModelSignalGenerator):
    def __init__(
        self,
        pipeline: FeaturePipeline,
        predictor: Predictor,
        manifest: ArtifactManifest,
        decision: DecisionPolicy,
    ) -> None:
        self._pipeline = pipeline
        self._predictor = predictor
        self._manifest = manifest
        self._decision = decision
        self._last_attempt: dict[str, datetime] = {}

    def market_requirements(self) -> MarketDataRequirements:
        return MarketDataRequirements(
            bars=self._pipeline.required_bars(),
            context_symbols=frozenset({self._pipeline.benchmark_symbol}),
        )

    def generate(
        self,
        symbol: str,
        snapshot: MarketSnapshot,
        position: Position | None,
    ) -> ExposureIntent:
        bars = snapshot.bars.get(symbol, [])
        safety_time = bars[-1].timestamp if bars else snapshot.as_of
        # Session safety is independent of feature/model availability.
        if (
            position is not None
            and minutes_until_close(safety_time) <= self._decision.flatten_minutes
        ):
            return ExposureIntent(
                symbol=symbol,
                target=TargetExposure.FLAT,
                model_id=self._manifest.model_id,
                schema_hash=self._pipeline.schema_hash,
                reason="mandatory end-of-day flatten",
                timestamp=safety_time,
            )
        if not bars:
            return self._unchanged(symbol, snapshot.as_of, "no completed bars")
        bar_time = bars[-1].timestamp
        if self._last_attempt.get(symbol) == bar_time:
            return self._unchanged(symbol, bar_time, "bar already evaluated")
        self._last_attempt[symbol] = bar_time

        vector = self._pipeline.compute(
            bars,
            snapshot.bars.get(self._pipeline.benchmark_symbol, []),
        )
        if vector is None:
            return self._unchanged(symbol, bar_time, "features warming up or unavailable")
        try:
            prediction = self._predictor.predict(vector)
            target, reason = self._decision.decide_at_timestamp(prediction, position, bar_time)
        except Exception:
            logger.exception("%s: model prediction failed", symbol)
            return self._unchanged(symbol, bar_time, "model prediction failed")
        return ExposureIntent(
            symbol=symbol,
            target=target,
            model_id=self._manifest.model_id,
            schema_hash=vector.schema_hash,
            prediction=prediction,
            features=vector,
            reason=reason,
            timestamp=bar_time,
        )

    def _unchanged(self, symbol: str, timestamp: datetime, reason: str) -> ExposureIntent:
        return ExposureIntent(
            symbol=symbol,
            target=TargetExposure.UNCHANGED,
            model_id=self._manifest.model_id,
            schema_hash=self._pipeline.schema_hash,
            reason=reason,
            timestamp=timestamp,
        )


def build_signal_generator(
    config: ModelConfig,
    evaluation_start: date | None = None,
) -> XGBoostSignalGenerator:
    if config.artifact_path is None:
        raise ValueError("model.artifact_path must be explicitly selected for this command")
    pipeline = FeaturePipeline(
        config.features,
        config.timeframe_minutes,
        config.benchmark_symbol,
    )
    manifest, model_path = validate_artifact(
        config.artifact_path,
        pipeline,
        config,
        evaluation_start=evaluation_start,
    )
    from carcharoth.services.xgboost import XGBoostPredictor

    predictor = XGBoostPredictor(model_path, manifest.task, tuple(manifest.feature_names))
    return XGBoostSignalGenerator(
        pipeline=pipeline,
        predictor=predictor,
        manifest=manifest,
        decision=DecisionPolicy(config.decision),
    )
