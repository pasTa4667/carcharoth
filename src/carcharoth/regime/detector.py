"""Combines feature evidence into a per-symbol regime assessment.

Pure — no I/O. Directional evidence (trend vs mean reversion) is averaged
by weight; stability evidence (change detection) attenuates the result,
because directional statistics computed over a window straddling a regime
break are unreliable. The regime is whichever side of the axis the final
score lands on ("always pick best").
"""

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from carcharoth.domain.models import Bar
from carcharoth.regime.features.base import RegimeFeature
from carcharoth.regime.models import Evidence, Regime, RegimeAssessment


class RegimeDetector:
    def __init__(
        self,
        features: Sequence[tuple[RegimeFeature, float]],
        lookback: int,
        winsorize_sigma: float = 5.0,
    ) -> None:
        if not features:
            raise ValueError("at least one feature is required")
        if any(weight <= 0 for _, weight in features):
            raise ValueError("feature weights must be > 0")
        if winsorize_sigma <= 0:
            raise ValueError("winsorize_sigma must be > 0")
        self._features = list(features)
        self._lookback = lookback
        self._winsorize_sigma = winsorize_sigma

    def required_lookback(self) -> int:
        """Bars needed for every feature to emit evidence."""
        return max(self._lookback, 1 + max(f.min_returns() for f, _ in self._features))

    def assess(self, symbol: str, bars: Sequence[Bar]) -> RegimeAssessment | None:
        """Assess the regime, or None while no directional feature has
        enough history to place the symbol on the trend/mean-reversion axis."""
        returns = self._log_returns(bars)
        if returns is None:
            return None

        evidence: list[Evidence] = []
        weights: dict[str, float] = {}
        for feature, weight in self._features:
            result = feature.compute(returns)
            if result is None:
                continue
            evidence.append(result)
            weights[result.feature] = weight

        directional = [e for e in evidence if e.direction is not None]
        if not directional:
            return None
        total_weight = sum(weights[e.feature] for e in directional)
        directional_score = (
            sum(weights[e.feature] * e.direction for e in directional if e.direction is not None)
            / total_weight
        )

        stabilities = [e.stability for e in evidence if e.stability is not None]
        stability = min(stabilities) if stabilities else 1.0

        score = directional_score * stability
        regime = Regime.TRENDING if score > 0 else Regime.MEAN_REVERTING
        return RegimeAssessment(
            symbol=symbol,
            regime=regime,
            score=score,
            directional_score=directional_score,
            stability=stability,
            evidence=tuple(evidence),
        )

    def weight_of(self, feature_name: str) -> float | None:
        """The configured weight for a feature, for persistence/audit."""
        return next((w for f, w in self._features if f.name == feature_name), None)

    def _log_returns(self, bars: Sequence[Bar]) -> npt.NDArray[np.float64] | None:
        """Winsorized log returns of closes; None when fewer than two usable
        closes exist. Clipping at +/- winsorize_sigma sigma blunts overnight
        gaps that would otherwise dominate multi-session intraday windows."""
        closes = np.array([bar.close for bar in bars[-self._lookback :]], dtype=np.float64)
        closes = closes[closes > 0]
        if len(closes) < 2:
            return None
        returns = np.diff(np.log(closes))
        std = float(returns.std())
        if std > 0:
            limit = self._winsorize_sigma * std
            returns = np.clip(returns, -limit, limit)
        return returns
