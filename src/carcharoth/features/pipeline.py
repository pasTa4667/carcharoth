"""Ordered, versioned feature computation shared by training and inference."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

import numpy as np
import numpy.typing as npt

from carcharoth.config.app_config import FeatureSpecConfig, ModelConfig, ModelHmmConfig
from carcharoth.domain.models import Bar, BarSpec, FeatureVector, Timeframe
from carcharoth.features.base import Feature
from carcharoth.features.registry import build_feature
from carcharoth.features.session import regular_session_bars
from carcharoth.interfaces.cache import ByteStore
from carcharoth.performance import increment
from carcharoth.regime.hmm.context import (
    CONTEXT_IMPLEMENTATION_VERSION,
    HMM_CONTEXT_NAMES,
    HmmModelContext,
)


@dataclass(frozen=True, slots=True)
class FeatureDescriptor:
    registry_name: str
    output_name: str
    params: dict[str, Any]
    implementation_version: int

    def payload(self) -> dict[str, Any]:
        return {
            "registry_name": self.registry_name,
            "output_name": self.output_name,
            "params": self.params,
            "implementation_version": self.implementation_version,
        }


@dataclass(frozen=True, slots=True)
class _PreparedHistory:
    rows: Mapping[datetime, int]
    matrix: npt.NDArray[np.float64]
    names: tuple[str, ...]
    schema_hash: str

    def lookup(self, timestamp: datetime) -> FeatureVector | None:
        index = self.rows.get(timestamp)
        if index is None or not np.isfinite(self.matrix[index]).all():
            return None
        return FeatureVector(
            names=self.names,
            values=tuple(float(value) for value in self.matrix[index]),
            schema_hash=self.schema_hash,
            as_of=timestamp,
        )


class FeaturePipeline:
    """Computes one complete latest-row vector or no row at all."""

    def __init__(
        self,
        specs: Sequence[FeatureSpecConfig],
        timeframe_minutes: int,
        benchmark_symbol: str,
        hmm_config: ModelHmmConfig | None = None,
        hmm_store: ByteStore | None = None,
    ) -> None:
        if not specs:
            raise ValueError("at least one feature is required")
        self._timeframe = Timeframe.minutes(timeframe_minutes)
        self._benchmark_symbol = benchmark_symbol
        features: list[Feature] = []
        descriptors: list[FeatureDescriptor] = []
        for spec in specs:
            feature = build_feature(spec.name, dict(spec.params))
            features.append(feature)
            descriptors.append(
                FeatureDescriptor(
                    registry_name=spec.name,
                    output_name=feature.output_name,
                    params=dict(spec.params),
                    implementation_version=feature.implementation_version,
                )
            )
        names = [feature.output_name for feature in features]
        if len(names) != len(set(names)):
            duplicates = sorted({name for name in names if names.count(name) > 1})
            raise ValueError(f"duplicate feature output names: {duplicates}")
        self._features = tuple(features)
        self._hmm = (
            HmmModelContext(hmm_config, timeframe_minutes, hmm_store)
            if hmm_config is not None and hmm_config.enabled
            else None
        )
        if self._hmm is not None:
            for name in HMM_CONTEXT_NAMES:
                descriptors.append(
                    FeatureDescriptor(
                        registry_name="hmm_probability",
                        output_name=name,
                        params=self._hmm.descriptor_params | {"output": name},
                        implementation_version=CONTEXT_IMPLEMENTATION_VERSION,
                    )
                )
            names.extend(HMM_CONTEXT_NAMES)
        if len(names) != len(set(names)):
            raise ValueError("HMM output names conflict with configured model features")
        self._descriptors = tuple(descriptors)
        self._names = tuple(names)
        lookback = max(feature.required_bars(self._timeframe) for feature in features)
        if self._hmm is not None:
            lookback = max(lookback, self._hmm.required_bars())
        self._spec = BarSpec(timeframe=self._timeframe, lookback=lookback)
        self._prepared_hmm: dict[str, _PreparedHistory] = {}
        canonical = json.dumps(
            {
                "timeframe_minutes": timeframe_minutes,
                "benchmark_symbol": benchmark_symbol,
                "features": [descriptor.payload() for descriptor in descriptors],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self._schema_hash = hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()

    @property
    def benchmark_columns(self) -> tuple[int, ...]:
        return tuple(i for i, feature in enumerate(self._features) if feature.requires_benchmark)

    @property
    def requires_benchmark(self) -> bool:
        return bool(self.benchmark_columns)

    @property
    def benchmark_symbol(self) -> str:
        return self._benchmark_symbol

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self._names

    @property
    def schema_hash(self) -> str:
        return self._schema_hash

    @property
    def descriptors(self) -> tuple[FeatureDescriptor, ...]:
        return self._descriptors

    def required_bars(self) -> BarSpec:
        return self._spec

    def _compute_technical_matrix(
        self,
        symbol_bars: Sequence[Bar],
        benchmark_bars: Sequence[Bar],
    ) -> tuple[tuple[Bar, ...], npt.NDArray[np.float64]]:
        bars = tuple(sorted(regular_session_bars(symbol_bars), key=lambda bar: bar.timestamp))
        if not bars:
            return bars, np.empty((0, len(self._features)), dtype=np.float64)
        benchmark = tuple(
            sorted(
                regular_session_bars(benchmark_bars),
                key=lambda candidate: candidate.timestamp,
            )
        )
        columns = [
            feature.compute_series(bars, benchmark, self._timeframe) for feature in self._features
        ]
        return bars, np.column_stack(columns).astype(np.float64, copy=False)

    def compute_matrix(
        self,
        symbol_bars: Sequence[Bar],
        benchmark_bars: Sequence[Bar],
    ) -> tuple[tuple[Bar, ...], npt.NDArray[np.float64]]:
        """All feature rows in linear/vectorized passes (NaN during warm-up)."""
        increment("feature_matrices")
        bars, matrix = self._compute_technical_matrix(symbol_bars, benchmark_bars)
        if bars and self._hmm is not None:
            hmm = self._hmm.compute_series(bars[0].symbol, bars)
            matrix = np.column_stack((matrix, hmm))
        return bars, matrix

    def prepare_history(
        self,
        symbol_bars: Sequence[Bar],
        benchmark_bars: Sequence[Bar],
        *,
        start: datetime,
        end_exclusive: datetime,
    ) -> _PreparedHistory:
        for values in (symbol_bars, benchmark_bars):
            timestamps = [bar.timestamp for bar in values]
            if any(ts.tzinfo is None or ts.utcoffset() is None for ts in timestamps):
                raise ValueError("prepared history requires aware timestamps")
            if len(timestamps) != len(set(timestamps)):
                raise ValueError("duplicate timestamps in prepared history input")
        bars, matrix = self.compute_matrix(symbol_bars, benchmark_bars)
        retained = [i for i, bar in enumerate(bars) if start <= bar.timestamp < end_exclusive]
        predecessor = next(
            (i for i in range(len(bars) - 1, -1, -1) if bars[i].timestamp < start), None
        )
        if predecessor is not None:
            retained.insert(0, predecessor)
        compact = matrix[retained].copy()
        compact.flags.writeable = False
        return _PreparedHistory(
            MappingProxyType({bars[i].timestamp: row for row, i in enumerate(retained)}),
            compact,
            self._names,
            self._schema_hash,
        )

    def prepare_hmm_history(self, symbol: str, bars: Sequence[Bar]) -> None:
        """Prepare only HMM context for replay while technical features stay rolling."""
        if self._hmm is None:
            return
        ordered = tuple(sorted(regular_session_bars(bars), key=lambda bar: bar.timestamp))
        matrix = self._hmm.compute_series(symbol, ordered)
        matrix.flags.writeable = False
        self._prepared_hmm[symbol] = _PreparedHistory(
            MappingProxyType({bar.timestamp: i for i, bar in enumerate(ordered)}),
            matrix,
            HMM_CONTEXT_NAMES,
            self._schema_hash,
        )

    def compute(
        self,
        symbol_bars: Sequence[Bar],
        benchmark_bars: Sequence[Bar],
    ) -> FeatureVector | None:
        # Technical features remain vectorized over bounded rolling history. HMM
        # context is either a precomputed replay lookup or one incremental detector.
        hmm = self._hmm
        increment("feature_matrices")
        bars, matrix = self._compute_technical_matrix(symbol_bars, benchmark_bars)
        if not bars:
            return None
        values = matrix[-1]
        if hmm is not None:
            symbol = bars[-1].symbol
            prepared = self._prepared_hmm.get(symbol)
            context = prepared.lookup(bars[-1].timestamp) if prepared is not None else None
            hmm_values = context.values if context is not None else hmm.latest(symbol, bars)
            if hmm_values is None:
                return None
            values = np.concatenate((values, np.asarray(hmm_values, dtype=np.float64)))
        if not np.isfinite(values).all():
            return None
        return FeatureVector(
            names=self._names,
            values=tuple(float(value) for value in values),
            schema_hash=self._schema_hash,
            as_of=bars[-1].timestamp,
        )


def build_feature_pipeline(
    config: ModelConfig, hmm_store: ByteStore | None = None
) -> FeaturePipeline:
    """The single schema construction path for training and inference."""
    return FeaturePipeline(
        config.features,
        config.timeframe_minutes,
        config.benchmark_symbol,
        config.hmm,
        hmm_store,
    )
