"""Persistent cache for fitted HMM models.

Fitting (n_restarts x EM) dominates the detector's cost and is fully
deterministic given the observation matrix, the symbol (per-symbol restart
seeds) and the fit parameters — a cached fit is bit-identical to a fresh
one. Keys hash the exact training input plus every config field that shapes
the output, plus the installed hmmlearn version (pickled models are not
portable across versions). ``fitted_through`` always comes from the current
call, never the cache, so the refit cadence is unchanged.
"""

import hashlib
import json
import logging
import pickle
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import hmmlearn
import numpy as np
import numpy.typing as npt
from hmmlearn.hmm import GaussianHMM

from carcharoth.interfaces.cache import ByteStore
from carcharoth.regime.hmm.detector import SymbolModel
from carcharoth.regime.models import Regime

logger = logging.getLogger(__name__)

HMM_PREFIX = "carch:hmm:v1:"


def hmm_config_hash(fields: Mapping[str, object]) -> str:
    """Canonical hash of the fit-relevant detector config fields."""
    payload = dict(fields) | {"hmmlearn": hmmlearn.__version__}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()


@dataclass(frozen=True, slots=True)
class _FitPayload:
    """The pickled value: everything in SymbolModel except fitted_through."""

    model: GaussianHMM
    scaler_mean: npt.NDArray[np.float64]
    scaler_std: npt.NDArray[np.float64]
    labels: Mapping[int, Regime]


class HmmFitCache:
    """Keyed by (config hash, symbol, exact observation matrix)."""

    def __init__(self, store: ByteStore, config_hash: str) -> None:
        self._store = store
        self._config_hash = config_hash

    def load(
        self, symbol: str, observations: npt.NDArray[np.float64], fitted_through: datetime
    ) -> SymbolModel | None:
        payload = self._store.get(self._key(symbol, observations))
        if payload is None:
            return None
        try:
            value = pickle.loads(payload)  # self-produced values, local redis only
        except Exception:
            logger.warning("hmm fit cache: dropping unreadable payload", exc_info=True)
            return None
        if not isinstance(value, _FitPayload):
            return None
        return SymbolModel(
            model=value.model,
            scaler_mean=value.scaler_mean,
            scaler_std=value.scaler_std,
            labels=value.labels,
            fitted_through=fitted_through,  # the current call's horizon, never the cache's
        )

    def store(self, symbol: str, observations: npt.NDArray[np.float64], model: SymbolModel) -> None:
        payload = _FitPayload(
            model=model.model,
            scaler_mean=model.scaler_mean,
            scaler_std=model.scaler_std,
            labels=model.labels,
        )
        self._store.set(self._key(symbol, observations), pickle.dumps(payload, protocol=5))

    def _key(self, symbol: str, observations: npt.NDArray[np.float64]) -> str:
        digest = hashlib.blake2b(digest_size=16)
        digest.update(repr(observations.shape).encode())
        digest.update(np.ascontiguousarray(observations, dtype=np.float64).tobytes())
        return f"{HMM_PREFIX}{self._config_hash}:{symbol}:{digest.hexdigest()}"
