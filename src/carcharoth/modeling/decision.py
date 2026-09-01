"""Prediction -> target exposure, including hysteresis and session safety."""

from __future__ import annotations

from datetime import datetime

from carcharoth.config.app_config import DecisionPolicyConfig
from carcharoth.domain.models import ModelTask, Position, Prediction, TargetExposure
from carcharoth.features.session import minutes_since_open, minutes_until_close


class DecisionPolicy:
    def __init__(self, config: DecisionPolicyConfig) -> None:
        self._config = config

    @property
    def flatten_minutes(self) -> int:
        return self._config.flatten_minutes

    def decide(
        self,
        prediction: Prediction,
        position: Position | None,
        minutes_since_session_open: float,
        minutes_to_session_close: float,
    ) -> tuple[TargetExposure, str]:
        current = position.exposure if position is not None else TargetExposure.FLAT
        if position is not None and minutes_to_session_close <= self._config.flatten_minutes:
            return TargetExposure.FLAT, "mandatory end-of-day flatten"

        target = (
            self._regression(prediction, current)
            if prediction.task is ModelTask.REGRESSION
            else self._classification(prediction)
        )
        entries_allowed = (
            minutes_since_session_open >= self._config.entry_delay_minutes
            and minutes_to_session_close > self._config.entry_cutoff_minutes
        )
        if current is TargetExposure.FLAT and target in {
            TargetExposure.LONG,
            TargetExposure.SHORT,
        }:
            if not entries_allowed:
                return TargetExposure.FLAT, "entry blocked by session window"
        elif (
            current in {TargetExposure.LONG, TargetExposure.SHORT}
            and target not in {current, TargetExposure.FLAT}
            and not entries_allowed
        ):
            return TargetExposure.FLAT, "opposite entry blocked; close before session end"
        return target, self._reason(prediction, target)

    def decide_at_timestamp(
        self, prediction: Prediction, position: Position | None, timestamp: datetime
    ) -> tuple[TargetExposure, str]:
        return self.decide(
            prediction,
            position,
            minutes_since_open(timestamp),
            minutes_until_close(timestamp),
        )

    def _regression(self, prediction: Prediction, current: TargetExposure) -> TargetExposure:
        value = prediction.expected_return
        assert value is not None
        config = self._config
        if current is TargetExposure.LONG:
            if value >= config.long_exit_threshold:
                return TargetExposure.LONG
            if value <= config.short_entry_threshold:
                return TargetExposure.SHORT
            return TargetExposure.FLAT
        if current is TargetExposure.SHORT:
            if value <= config.short_exit_threshold:
                return TargetExposure.SHORT
            if value >= config.long_entry_threshold:
                return TargetExposure.LONG
            return TargetExposure.FLAT
        if value >= config.long_entry_threshold:
            return TargetExposure.LONG
        if value <= config.short_entry_threshold:
            return TargetExposure.SHORT
        return TargetExposure.FLAT

    def _classification(self, prediction: Prediction) -> TargetExposure:
        target, probability = max(prediction.probabilities.items(), key=lambda item: item[1])
        if probability < self._config.class_probability_threshold:
            return TargetExposure.FLAT
        return target

    @staticmethod
    def _reason(prediction: Prediction, target: TargetExposure) -> str:
        if prediction.expected_return is not None:
            return f"expected_return={prediction.expected_return:.6f} -> {target.value}"
        probability = prediction.probabilities.get(target, 0.0)
        return f"class={target.value} probability={probability:.4f}"
