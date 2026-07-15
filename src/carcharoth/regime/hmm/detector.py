"""Gaussian-HMM regime detector.

One lazily-fitted model per symbol, refit after ~a session of new bars.
Assessment is the posterior state distribution of the newest bar (the
forward-backward posterior of the last observation equals the filtered
posterior — no lookahead), mapped to regimes via the emission-mean labeling.

Pure w.r.t. I/O; keeps per-symbol model state between calls (the detector
lives for exactly one run). An optional injected fit cache skips re-fitting
when the exact training input has been fitted before — fitting is
seed-deterministic, so a cached fit is bit-identical to a fresh one.
"""

import logging
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt
from hmmlearn.hmm import GaussianHMM

from carcharoth.domain.models import Bar
from carcharoth.interfaces.regime_detector import RegimeDetector
from carcharoth.regime.hmm.features import (
    FEATURE_NAMES,
    build_feature_matrix,
    drop_warmup,
    warmup_rows,
)
from carcharoth.regime.hmm.labeling import label_states
from carcharoth.regime.models import Evidence, Regime, RegimeAssessment

if TYPE_CHECKING:
    from carcharoth.regime.hmm.fit_cache import HmmFitCache

logger = logging.getLogger(__name__)

_SCALER_EPS = 1e-12


@dataclass(frozen=True, slots=True)
class SymbolModel:
    """One symbol's fitted model plus everything inference needs."""

    model: GaussianHMM
    scaler_mean: npt.NDArray[np.float64]
    scaler_std: npt.NDArray[np.float64]
    labels: Mapping[int, Regime]
    fitted_through: datetime  # newest bar timestamp at fit time


class HmmRegimeDetector(RegimeDetector):
    def __init__(
        self,
        *,
        n_states: int = 4,
        training_window: int = 1560,
        refit_interval_bars: int = 78,
        vol_window: int = 20,
        ema_period: int = 50,
        adx_period: int = 14,
        winsorize_sigma: float = 5.0,
        covariance_type: str = "diag",
        n_iter: int = 100,
        tol: float = 1e-3,
        min_covar: float = 1e-3,
        n_restarts: int = 2,
        seed: int = 42,
        fit_cache: "HmmFitCache | None" = None,
    ) -> None:
        if n_states < 4:
            raise ValueError("n_states must be >= 4 (one per regime)")
        if training_window < 2:
            raise ValueError("training_window must be > 1")
        if refit_interval_bars < 1:
            raise ValueError("refit_interval_bars must be >= 1")
        if n_restarts < 1:
            raise ValueError("n_restarts must be >= 1")
        if winsorize_sigma <= 0:
            raise ValueError("winsorize_sigma must be > 0")
        self._n_states = n_states
        self._training_window = training_window
        self._refit_interval_bars = refit_interval_bars
        self._vol_window = vol_window
        self._ema_period = ema_period
        self._adx_period = adx_period
        self._winsorize_sigma = winsorize_sigma
        self._covariance_type = covariance_type
        self._n_iter = n_iter
        self._tol = tol
        self._min_covar = min_covar
        self._n_restarts = n_restarts
        self._seed = seed
        self._fit_cache = fit_cache
        self._models: dict[str, SymbolModel] = {}

    def required_lookback(self) -> int:
        """Training window plus the indicators' warm-up rows."""
        return self._training_window + warmup_rows(
            self._vol_window, self._ema_period, self._adx_period
        )

    def assess(self, symbol: str, bars: Sequence[Bar]) -> RegimeAssessment | None:
        if len(bars) < self.required_lookback():
            return None
        matrix = build_feature_matrix(
            bars[-self.required_lookback() :],
            vol_window=self._vol_window,
            ema_period=self._ema_period,
            adx_period=self._adx_period,
            winsorize_sigma=self._winsorize_sigma,
        )
        if matrix is None:
            return None
        observations = drop_warmup(matrix)
        if len(observations) < self._training_window:
            return None
        observations = observations[-self._training_window :]

        record = self._models.get(symbol)
        if record is None or self._bars_since_fit(bars, record) >= self._refit_interval_bars:
            fitted = None
            if self._fit_cache is not None:
                fitted = self._fit_cache.load(
                    symbol, observations, fitted_through=bars[-1].timestamp
                )
            if fitted is None:
                fitted = self._fit(symbol, observations, fitted_through=bars[-1].timestamp)
                if fitted is not None and self._fit_cache is not None:
                    self._fit_cache.store(symbol, observations, fitted)
            if fitted is not None:
                record = fitted
                self._models[symbol] = record
        if record is None:
            return None  # never fitted successfully: indistinguishable from warm-up

        posterior = self._posterior(record, observations)
        probabilities: dict[Regime, float] = {
            Regime.TRENDING_UP: 0.0,
            Regime.TRENDING_DOWN: 0.0,
            Regime.RANGE_BOUND: 0.0,
            Regime.HIGH_VOLATILITY: 0.0,
        }
        for state, prob in enumerate(posterior):
            probabilities[record.labels[state]] += float(prob)

        regime = max(probabilities, key=lambda r: probabilities[r])
        raw_last = matrix[~np.isnan(matrix).any(axis=1)][-1]
        evidence = tuple(
            Evidence(feature=name, value=float(raw_last[i])) for i, name in enumerate(FEATURE_NAMES)
        )
        return RegimeAssessment(
            symbol=symbol,
            regime=regime,
            score=probabilities[regime],
            directional_score=probabilities[Regime.TRENDING_UP]
            - probabilities[Regime.TRENDING_DOWN],
            stability=1.0 - probabilities[Regime.HIGH_VOLATILITY],
            evidence=evidence,
            probabilities=probabilities,
        )

    def _bars_since_fit(self, bars: Sequence[Bar], record: SymbolModel) -> int:
        """Bars newer than the model's fit horizon (counted from the end)."""
        count = 0
        for bar in reversed(bars):
            if bar.timestamp <= record.fitted_through:
                break
            count += 1
        return count

    def _fit(
        self, symbol: str, observations: npt.NDArray[np.float64], fitted_through: datetime
    ) -> SymbolModel | None:
        """Fit a fresh model (best of n_restarts); None if every fit fails —
        the caller keeps the previous model if one exists."""
        scaler_mean = observations.mean(axis=0)
        scaler_std = observations.std(axis=0)
        scaler_std = np.where(scaler_std < _SCALER_EPS, 1.0, scaler_std)
        scaled = (observations - scaler_mean) / scaler_std

        best: GaussianHMM | None = None
        best_loglik = -np.inf
        for restart_seed in self._symbol_seeds(symbol):
            model = GaussianHMM(
                n_components=self._n_states,
                covariance_type=self._covariance_type,
                n_iter=self._n_iter,
                tol=self._tol,
                min_covar=self._min_covar,
                random_state=restart_seed,
            )
            try:
                model.fit(scaled)
            except Exception:
                logger.warning("%s: hmm fit failed", symbol, exc_info=True)
                continue
            if not self._is_valid_fit(model):
                logger.warning("%s: hmm fit produced a degenerate model, discarding", symbol)
                continue
            loglik = float(model.score(scaled))
            if loglik > best_loglik:
                best, best_loglik = model, loglik

        if best is None:
            return None
        labels = label_states(np.asarray(best.means_, dtype=np.float64))
        logger.info(
            "%s: hmm fitted through %s (loglik %.1f), labels %s",
            symbol,
            fitted_through.isoformat(),
            best_loglik,
            {state: regime.value for state, regime in sorted(labels.items())},
        )
        return SymbolModel(
            model=best,
            scaler_mean=scaler_mean,
            scaler_std=scaler_std,
            labels=labels,
            fitted_through=fitted_through,
        )

    def _symbol_seeds(self, symbol: str) -> list[int]:
        """Deterministic per-symbol restart seeds (crc32, not the process-
        salted hash())."""
        sequence = np.random.SeedSequence([self._seed, zlib.crc32(symbol.encode())])
        return [int(s.generate_state(1)[0]) for s in sequence.spawn(self._n_restarts)]

    def _posterior(
        self, record: SymbolModel, observations: npt.NDArray[np.float64]
    ) -> npt.NDArray[np.float64]:
        """State distribution of the newest observation, in the model's
        frozen feature scaling."""
        scaled = (observations - record.scaler_mean) / record.scaler_std
        posteriors = record.model.predict_proba(scaled)
        return np.asarray(posteriors[-1], dtype=np.float64)

    def _is_valid_fit(self, model: GaussianHMM) -> bool:
        params = (model.transmat_, model.means_, model.covars_, model.startprob_)
        return all(np.all(np.isfinite(np.asarray(p))) for p in params)
