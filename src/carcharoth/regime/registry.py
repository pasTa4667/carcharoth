"""Regime feature registry: maps config names to feature factories.

Adding a new feature: implement the RegimeFeature interface in a new module
under regime/features/ and add one entry here. Nothing else changes.
"""

from collections.abc import Callable
from typing import Any

from carcharoth.regime.features import (
    CusumFeature,
    HurstFeature,
    RegimeFeature,
    VolClusteringFeature,
    WassersteinFeature,
)

FEATURES: dict[str, Callable[..., RegimeFeature]] = {
    HurstFeature.name: HurstFeature,
    VolClusteringFeature.name: VolClusteringFeature,
    CusumFeature.name: CusumFeature,
    WassersteinFeature.name: WassersteinFeature,
}


def build_feature(name: str, params: dict[str, Any]) -> RegimeFeature:
    try:
        factory = FEATURES[name]
    except KeyError:
        available = ", ".join(sorted(FEATURES))
        raise ValueError(f"unknown regime feature {name!r}; available: {available}") from None
    return factory(**params)
