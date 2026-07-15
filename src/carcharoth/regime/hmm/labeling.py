"""Maps the HMM's anonymous hidden states to named regimes.

EM assigns no meaning to state indices, so after every fit the states are
labeled from their emission means (in standardized feature space): the
highest-volatility state becomes HIGH_VOLATILITY; the remaining states are
ranked by a trend score (mean return + EMA distance) — max is TRENDING_UP,
min is TRENDING_DOWN, the rest are RANGE_BOUND. The ordering is total, so
the labeling is always a unique, reproducible assignment.
"""

import logging

import numpy as np
import numpy.typing as npt

from carcharoth.regime.hmm.features import COL_EMA_DISTANCE, COL_RETURN, COL_VOLATILITY
from carcharoth.regime.models import Regime

logger = logging.getLogger(__name__)

#: below this spread (in standardized units) between the extreme trend
#: scores, or between the vol-max state and the median vol, the states are
#: barely separated and labels are unreliable
_WEAK_SEPARATION = 0.5


def label_states(means: npt.NDArray[np.float64]) -> dict[int, Regime]:
    """State index -> regime, from the fitted emission means.

    `means` is the HMM's (n_states, n_features) emission-mean matrix in
    standardized feature space. Requires at least 4 states.
    """
    n_states = means.shape[0]
    if n_states < 4:
        raise ValueError(f"labeling needs at least 4 states, got {n_states}")

    volatilities = means[:, COL_VOLATILITY]
    # ties broken by the lower state index (argmax picks the first maximum)
    high_vol_state = int(np.argmax(volatilities))

    rest = [i for i in range(n_states) if i != high_vol_state]
    trend_scores = {i: float(means[i, COL_RETURN] + means[i, COL_EMA_DISTANCE]) for i in rest}
    # sort by (score, index): a total order, so ties still label deterministically
    ranked = sorted(rest, key=lambda i: (trend_scores[i], i))

    labels = {high_vol_state: Regime.HIGH_VOLATILITY}
    labels[ranked[-1]] = Regime.TRENDING_UP
    labels[ranked[0]] = Regime.TRENDING_DOWN
    for middle in ranked[1:-1]:
        labels[middle] = Regime.RANGE_BOUND

    trend_spread = trend_scores[ranked[-1]] - trend_scores[ranked[0]]
    vol_spread = float(volatilities[high_vol_state] - np.median(volatilities))
    if trend_spread < _WEAK_SEPARATION or vol_spread < _WEAK_SEPARATION:
        logger.warning(
            "hmm states barely separated (trend spread %.2f, vol spread %.2f); "
            "labels may be unreliable",
            trend_spread,
            vol_spread,
        )
    return labels
