"""Domain types for market regime detection.

A regime assessment combines evidence from independent statistical features
into a single score on a trend <-> mean-reversion axis; the engine uses it
to decide which strategy trades a symbol.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Regime(StrEnum):
    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One feature's contribution to a regime assessment.

    `direction` places the market on the trend (+1) <-> mean-reversion (-1)
    axis; `stability` says how settled the current regime looks (1 = stable,
    0 = regime break in progress). A feature emits whichever fields apply.
    """

    feature: str
    value: float
    direction: float | None = None
    stability: float | None = None


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
