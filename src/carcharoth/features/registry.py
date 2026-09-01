"""Config name -> scalar feature builder."""

from collections.abc import Callable
from typing import Any

from carcharoth.features.base import Feature
from carcharoth.features.technical import (
    AdxFeature,
    BenchmarkReturnFeature,
    EmaDistanceFeature,
    LogReturnFeature,
    NormalizedAtrFeature,
    NormalizedRsiFeature,
    RangePositionFeature,
    RealizedVolatilityFeature,
    SessionVwapDistanceFeature,
    TimeOfDayFeature,
    TimeOfDayVolumeZScoreFeature,
)

FeatureFactory = Callable[[dict[str, Any]], Feature]


def _positive_int(params: dict[str, Any], key: str) -> int:
    value = params.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"feature param {key!r} must be a positive integer")
    return value


def _period(factory: Callable[[int], Feature]) -> FeatureFactory:
    return lambda params: factory(_positive_int(params, "period"))


def _no_params(factory: Callable[[], Feature]) -> FeatureFactory:
    def build(params: dict[str, Any]) -> Feature:
        if params:
            raise ValueError(f"feature takes no params, got {sorted(params)}")
        return factory()

    return build


def _time(params: dict[str, Any]) -> Feature:
    component = params.get("component")
    if component not in {"sin", "cos"}:
        raise ValueError("time_of_day.component must be 'sin' or 'cos'")
    return TimeOfDayFeature(str(component))


FEATURES: dict[str, FeatureFactory] = {
    "log_return": _period(LogReturnFeature),
    "ema_distance": _period(EmaDistanceFeature),
    "adx": _period(AdxFeature),
    "normalized_atr": _period(NormalizedAtrFeature),
    "realized_volatility": _period(RealizedVolatilityFeature),
    "normalized_rsi": _period(NormalizedRsiFeature),
    "range_position": _period(RangePositionFeature),
    "session_vwap_distance": _no_params(SessionVwapDistanceFeature),
    "time_of_day_volume_zscore": lambda params: TimeOfDayVolumeZScoreFeature(
        _positive_int(params, "sessions")
    ),
    "benchmark_return": _period(BenchmarkReturnFeature),
    "time_of_day": _time,
}


def build_feature(name: str, params: dict[str, Any]) -> Feature:
    try:
        factory = FEATURES[name]
    except KeyError:
        raise ValueError(
            f"unknown model feature {name!r}; available: {', '.join(sorted(FEATURES))}"
        ) from None
    return factory(params)
