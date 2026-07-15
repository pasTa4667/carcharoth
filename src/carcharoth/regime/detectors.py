"""Regime detector builders: config -> concrete detector.

Mirrors strategies/registry.py. Adding a detector: implement the
RegimeDetector ABC, add a config section in app_config.py, and add one
builder here. Nothing else changes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from carcharoth.regime.hmm import HmmRegimeDetector
from carcharoth.regime.hmm.fit_cache import HmmFitCache, hmm_config_hash
from carcharoth.regime.models import Regime
from carcharoth.regime.registry import build_feature
from carcharoth.regime.score_detector import ScoreRegimeDetector

if TYPE_CHECKING:
    from carcharoth.config.app_config import RegimeConfig
    from carcharoth.interfaces.cache import ByteStore
    from carcharoth.interfaces.regime_detector import RegimeDetector

logger = logging.getLogger(__name__)

#: which regimes each detector can emit (for config sanity warnings)
EMITTED_REGIMES: dict[str, frozenset[Regime]] = {
    "score": frozenset({Regime.TRENDING, Regime.MEAN_REVERTING}),
    "hmm": frozenset(
        {
            Regime.TRENDING_UP,
            Regime.TRENDING_DOWN,
            Regime.RANGE_BOUND,
            Regime.HIGH_VOLATILITY,
        }
    ),
}


def build_detector(
    config: RegimeConfig, *, hmm_fit_store: ByteStore | None = None
) -> RegimeDetector:
    """The detector selected by `regime.detector`, built from its section.

    ``hmm_fit_store`` (optional) backs a persistent cache of HMM fits keyed
    by the fit-relevant config fields — ``evaluate_interval_minutes`` and
    ``min_confidence`` are excluded because they don't affect fit output.
    """
    mapped = {Regime(name) for name in config.regimes}
    emitted = EMITTED_REGIMES[config.detector]
    if not mapped & emitted:
        logger.warning(
            "no mapped regime (%s) can be emitted by the %r detector (%s); nothing will ever trade",
            ", ".join(sorted(r.value for r in mapped)),
            config.detector,
            ", ".join(sorted(r.value for r in emitted)),
        )

    if config.detector == "score":
        assert config.score is not None  # enforced by RegimeConfig's validator
        return ScoreRegimeDetector(
            features=[
                (build_feature(name, fc.params), fc.weight)
                for name, fc in config.score.features.items()
            ],
            lookback=config.score.lookback,
            winsorize_sigma=config.score.winsorize_sigma,
        )
    assert config.hmm is not None  # enforced by RegimeConfig's validator
    fit_cache = None
    if hmm_fit_store is not None:
        fields = config.hmm.model_dump(exclude={"evaluate_interval_minutes", "min_confidence"})
        fit_cache = HmmFitCache(hmm_fit_store, hmm_config_hash(fields))
    return HmmRegimeDetector(
        fit_cache=fit_cache,
        n_states=config.hmm.n_states,
        training_window=config.hmm.training_window,
        refit_interval_bars=config.hmm.refit_interval_bars,
        vol_window=config.hmm.vol_window,
        ema_period=config.hmm.ema_period,
        adx_period=config.hmm.adx_period,
        winsorize_sigma=config.hmm.winsorize_sigma,
        covariance_type=config.hmm.covariance_type,
        n_iter=config.hmm.n_iter,
        tol=config.hmm.tol,
        min_covar=config.hmm.min_covar,
        n_restarts=config.hmm.n_restarts,
        seed=config.hmm.seed,
    )
