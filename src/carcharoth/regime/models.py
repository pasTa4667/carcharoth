"""Domain types for market regime detection.

A regime assessment combines evidence from independent statistical features
into a single score on a trend <-> mean-reversion axis; the engine uses it
to decide which strategy trades a symbol.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Regime(StrEnum):
    # score detector: single trend <-> mean-reversion axis
    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    # hmm detector: four hidden states
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGE_BOUND = "range_bound"
    HIGH_VOLATILITY = "high_volatility"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One feature's contribution to a regime assessment.

    `direction` places the market on the trend (+1) <-> mean-reversion (-1)
    axis; `stability` says how settled the current regime looks (1 = stable,
    0 = regime break in progress). A feature emits whichever fields apply.
    `weight` is assigned by the detector (not the feature) for audit.
    """

    feature: str
    value: float
    direction: float | None = None
    stability: float | None = None
    weight: float | None = None


@dataclass(frozen=True, slots=True)
class RegimeAssessment:
    """The detector's combined verdict for one symbol.

    `score = directional_score * stability`: a regime break attenuates the
    directional evidence, since statistics computed over a window straddling
    the break are unreliable.
    """

    symbol: str
    regime: Regime
    score: float
    directional_score: float
    stability: float
    evidence: tuple[Evidence, ...]
    #: full posterior over regimes, for detectors that produce one (HMM)
    probabilities: Mapping[Regime, float] | None = None


@dataclass(frozen=True, slots=True)
class StrategyAssignment:
    """Which strategy currently trades a symbol, and since when.

    Stores the strategy name (not the regime) so an assignment stays
    meaningful even if the regime -> strategy mapping is reconfigured.
    """

    symbol: str
    strategy: str
    regime: Regime
    since: datetime
