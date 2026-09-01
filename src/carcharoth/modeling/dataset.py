"""Leakage-safe global feature/target dataset construction."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import numpy.typing as npt

from carcharoth.config.app_config import (
    ClassificationTargetConfig,
    RegressionTargetConfig,
    TargetConfig,
)
from carcharoth.domain.models import Bar, ModelTask
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.features.session import MARKET_TZ

CLASS_SHORT = 0
CLASS_FLAT = 1
CLASS_LONG = 2


@dataclass(frozen=True, slots=True)
class DatasetRow:
    symbol: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class ModelDataset:
    features: npt.NDArray[np.float64]
    labels: npt.NDArray[np.float64] | npt.NDArray[np.int64]
    feature_names: tuple[str, ...]
    rows: tuple[DatasetRow, ...]
    task: ModelTask
    attempted_rows: int

    @property
    def complete_rows(self) -> int:
        return int(self.features.shape[0])


def build_dataset(
    bars_by_symbol: Mapping[str, list[Bar]],
    symbols: Sequence[str],
    pipeline: FeaturePipeline,
    target: TargetConfig,
    start: datetime,
    end_exclusive: datetime,
) -> ModelDataset:
    """Build rows whose feature time and full target lie inside one window.

    Features end at bar ``t``. Entry is ``open[t+1]`` and the horizon exit is
    ``close[t+horizon]``. Both must be in the same New York session as ``t``.
    Building train and validation independently is the horizon purge: no label
    can cross either window's end.
    """
    horizon = target.horizon_bars
    benchmark = bars_by_symbol.get(pipeline.benchmark_symbol, [])
    records: list[tuple[datetime, str, tuple[float, ...], float | int]] = []
    attempted = 0
    for symbol in symbols:
        bars, feature_matrix = pipeline.compute_matrix(bars_by_symbol.get(symbol, []), benchmark)
        for index, bar in enumerate(bars):
            if not start <= bar.timestamp < end_exclusive:
                continue
            attempted += 1
            future_index = index + horizon
            if index + 1 >= len(bars) or future_index >= len(bars):
                continue
            entry = bars[index + 1]
            exit_bar = bars[future_index]
            if exit_bar.timestamp >= end_exclusive:
                continue
            session_date = bar.timestamp.astimezone(MARKET_TZ).date()
            if (
                entry.timestamp.astimezone(MARKET_TZ).date() != session_date
                or exit_bar.timestamp.astimezone(MARKET_TZ).date() != session_date
            ):
                continue
            if entry.open <= 0 or exit_bar.close <= 0:
                continue
            feature_values = feature_matrix[index]
            if not np.isfinite(feature_values).all():
                continue
            forward_return = math.log(exit_bar.close / entry.open)
            label: float | int
            if isinstance(target, RegressionTargetConfig):
                label = forward_return
            elif isinstance(target, ClassificationTargetConfig):
                if forward_return < -target.neutral_band:
                    label = CLASS_SHORT
                elif forward_return > target.neutral_band:
                    label = CLASS_LONG
                else:
                    label = CLASS_FLAT
            else:  # pragma: no cover - pydantic discriminated union is exhaustive
                raise TypeError(f"unsupported target {type(target)!r}")
            records.append(
                (
                    bar.timestamp,
                    symbol,
                    tuple(float(value) for value in feature_values),
                    label,
                )
            )

    records.sort(key=lambda record: (record[0], record[1]))
    width = len(pipeline.feature_names)
    matrix = np.asarray([record[2] for record in records], dtype=np.float64)
    if not records:
        matrix = np.empty((0, width), dtype=np.float64)
    task = (
        ModelTask.REGRESSION
        if isinstance(target, RegressionTargetConfig)
        else ModelTask.CLASSIFICATION
    )
    labels: npt.NDArray[np.float64] | npt.NDArray[np.int64]
    if task is ModelTask.REGRESSION:
        labels = np.asarray([record[3] for record in records], dtype=np.float64)
    else:
        labels = np.asarray([record[3] for record in records], dtype=np.int64)
    return ModelDataset(
        features=matrix,
        labels=labels,
        feature_names=pipeline.feature_names,
        rows=tuple(DatasetRow(record[1], record[0]) for record in records),
        task=task,
        attempted_rows=attempted,
    )
