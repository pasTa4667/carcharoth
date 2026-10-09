"""Causal four-column HMM context for model training and inference."""

from __future__ import annotations

import hashlib
import logging
import pickle
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import numpy.typing as npt

from carcharoth.config.app_config import (
    HmmDetectorConfig,
    ModelHmmConfig,
    RegimeConfig,
)
from carcharoth.domain.models import Bar
from carcharoth.interfaces.cache import ByteStore
from carcharoth.interfaces.regime_detector import RegimeDetector
from carcharoth.performance import increment, stage
from carcharoth.regime.detectors import build_detector
from carcharoth.regime.hmm.fit_cache import hmm_config_hash
from carcharoth.regime.models import Regime

logger = logging.getLogger(__name__)

HMM_CONTEXT_NAMES = (
    "hmm_trending_up",
    "hmm_trending_down",
    "hmm_range_bound",
    "hmm_high_volatility",
)
_REGIMES = (
    Regime.TRENDING_UP,
    Regime.TRENDING_DOWN,
    Regime.RANGE_BOUND,
    Regime.HIGH_VOLATILITY,
)
_CONTEXT_PREFIX = "carch:hmm-context:v1:"
CONTEXT_IMPLEMENTATION_VERSION = 1


@dataclass(frozen=True, slots=True)
class HmmContextTable:
    timestamps: tuple[datetime, ...]
    values: npt.NDArray[np.float64]


class HistoricalHmmCache:
    """Exact-input cache for a symbol's timestamp-aligned posterior table."""

    def __init__(self, store: ByteStore, config_hash: str, timeframe_minutes: int) -> None:
        self._store = store
        self._config_hash = config_hash
        self._timeframe_minutes = timeframe_minutes

    def load(self, symbol: str, bars: Sequence[Bar]) -> HmmContextTable | None:
        payload = self._store.get(self._key(symbol, bars))
        if payload is None:
            return None
        try:
            table = pickle.loads(payload)  # self-produced values, local Redis only
            if not isinstance(table, HmmContextTable) or not self._valid(table, bars):
                return None
            table.values.flags.writeable = False
            return table
        except Exception:
            logger.warning("hmm context cache: dropping unreadable payload", exc_info=True)
            return None

    def store(self, symbol: str, bars: Sequence[Bar], table: HmmContextTable) -> None:
        self._store.set(self._key(symbol, bars), pickle.dumps(table, protocol=5))

    def _key(self, symbol: str, bars: Sequence[Bar]) -> str:
        digest = hashlib.blake2b(digest_size=16)
        for bar in bars:
            digest.update(bar.timestamp.isoformat().encode())
            digest.update(struct.pack("!5d", bar.open, bar.high, bar.low, bar.close, bar.volume))
        return (
            f"{_CONTEXT_PREFIX}{self._config_hash}:{self._timeframe_minutes}:"
            f"{symbol}:{len(bars)}:{digest.hexdigest()}"
        )

    @staticmethod
    def _valid(table: HmmContextTable, bars: Sequence[Bar]) -> bool:
        if table.timestamps != tuple(bar.timestamp for bar in bars):
            return False
        values = table.values
        if values.shape != (len(bars), len(HMM_CONTEXT_NAMES)):
            return False
        complete = np.isfinite(values).all(axis=1)
        if np.any(np.isfinite(values).any(axis=1) != complete):
            return False
        return bool(
            np.all((values[complete] >= 0) & (values[complete] <= 1))
            and np.allclose(values[complete].sum(axis=1), 1.0)
        )


class HmmModelContext:
    """Schedules one shared detector and exposes its four posteriors."""

    def __init__(
        self,
        config: ModelHmmConfig,
        timeframe_minutes: int,
        store: ByteStore | None = None,
    ) -> None:
        self._config = config
        self._timeframe_minutes = timeframe_minutes
        fields = config.model_dump(exclude={"enabled", "min_confidence"})
        self._config_hash = hmm_config_hash(fields)
        self._store = store
        self._table_cache = (
            HistoricalHmmCache(store, self._config_hash, timeframe_minutes)
            if store is not None
            else None
        )
        self._rolling_detector: RegimeDetector | None = None
        self._rolling_last_evaluation: dict[str, datetime] = {}
        self._rolling_values: dict[str, tuple[float, ...]] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return HMM_CONTEXT_NAMES

    @property
    def descriptor_params(self) -> dict[str, object]:
        return self._config.model_dump(exclude={"enabled", "min_confidence"}) | {
            "context_implementation_version": CONTEXT_IMPLEMENTATION_VERSION
        }

    def required_bars(self) -> int:
        detector = self._build_detector(None)
        return detector.required_lookback()

    def compute_series(self, symbol: str, bars: Sequence[Bar]) -> npt.NDArray[np.float64]:
        ordered = tuple(bars)
        if self._table_cache is not None:
            cached = self._table_cache.load(symbol, ordered)
            if cached is not None:
                increment("hmm_context_hits")
                return cached.values.copy()
        increment("hmm_context_misses")
        detector = self._build_detector(self._store)
        with stage("hmm_preparation"):
            values = self._schedule(detector, symbol, ordered)
        table = HmmContextTable(tuple(bar.timestamp for bar in ordered), values)
        if self._table_cache is not None:
            self._table_cache.store(symbol, ordered, table)
        return values

    def latest(self, symbol: str, bars: Sequence[Bar]) -> tuple[float, ...] | None:
        if not bars:
            return None
        timestamp = bars[-1].timestamp
        last = self._rolling_last_evaluation.get(symbol)
        if last is None or timestamp - last >= self._interval:
            detector = self._rolling_detector
            if detector is None:
                detector = self._build_detector(None)
                self._rolling_detector = detector
            increment("hmm_assessments")
            assessment = detector.assess(symbol, bars)
            if assessment is not None:
                self._rolling_last_evaluation[symbol] = timestamp
                assert assessment.probabilities is not None
                self._rolling_values[symbol] = tuple(
                    assessment.probabilities.get(regime, 0.0) for regime in _REGIMES
                )
        return self._rolling_values.get(symbol)

    @property
    def _interval(self) -> timedelta:
        return timedelta(minutes=self._config.evaluate_interval_minutes)

    def _schedule(
        self, detector: RegimeDetector, symbol: str, bars: Sequence[Bar]
    ) -> npt.NDArray[np.float64]:
        result = np.full((len(bars), len(HMM_CONTEXT_NAMES)), np.nan, dtype=np.float64)
        last_evaluation: datetime | None = None
        current: tuple[float, ...] | None = None
        for index, bar in enumerate(bars):
            if last_evaluation is None or bar.timestamp - last_evaluation >= self._interval:
                increment("hmm_assessments")
                assessment = detector.assess(symbol, bars[: index + 1])
                if assessment is not None:
                    last_evaluation = bar.timestamp
                    assert assessment.probabilities is not None
                    current = tuple(
                        assessment.probabilities.get(regime, 0.0) for regime in _REGIMES
                    )
            if current is not None:
                result[index] = current
        return result

    def _build_detector(self, store: ByteStore | None) -> RegimeDetector:
        hmm = HmmDetectorConfig.model_validate(
            self._config.model_dump(exclude={"enabled"})
        )
        return build_detector(RegimeConfig(detector="hmm", hmm=hmm), hmm_fit_store=store)
