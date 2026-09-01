"""Scale-free technical and context features for the global model."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
import talib

from carcharoth.domain.models import Bar, Timeframe, TimeframeUnit
from carcharoth.features.base import Feature, FeatureContext
from carcharoth.features.session import (
    MARKET_TZ,
    REGULAR_SESSION_MINUTES,
    current_session_bars,
)


def _closes(context: FeatureContext) -> npt.NDArray[np.float64]:
    return _close_array(context.bars)


def _close_array(bars: tuple[Bar, ...]) -> npt.NDArray[np.float64]:
    return np.asarray([bar.close for bar in bars], dtype=np.float64)


def _bars_per_session(timeframe: Timeframe) -> int:
    if timeframe.unit is not TimeframeUnit.MINUTE:
        raise ValueError("model features currently require a minute timeframe")
    return math.ceil(REGULAR_SESSION_MINUTES / timeframe.amount)


@dataclass(frozen=True, slots=True)
class LogReturnFeature(Feature):
    period: int
    implementation_version: int = 1

    @property
    def output_name(self) -> str:
        return f"return_{self.period}"

    def required_bars(self, timeframe: Timeframe) -> int:
        return self.period + 1

    def compute(self, context: FeatureContext) -> float | None:
        if len(context.bars) < self.period + 1:
            return None
        old, new = context.bars[-1 - self.period].close, context.bars[-1].close
        if old <= 0 or new <= 0:
            return None
        return math.log(new / old)

    def compute_series(
        self, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...], timeframe: Timeframe
    ) -> npt.NDArray[np.float64]:
        closes = _close_array(bars)
        result = np.full(len(bars), np.nan, dtype=np.float64)
        if len(bars) > self.period:
            valid = (closes[self.period :] > 0) & (closes[: -self.period] > 0)
            values = np.full(len(bars) - self.period, np.nan, dtype=np.float64)
            values[valid] = np.log(closes[self.period :][valid] / closes[: -self.period][valid])
            result[self.period :] = values
        return result


@dataclass(frozen=True, slots=True)
class EmaDistanceFeature(Feature):
    period: int
    implementation_version: int = 1

    @property
    def output_name(self) -> str:
        return f"ema_distance_{self.period}"

    def required_bars(self, timeframe: Timeframe) -> int:
        return self.period

    def compute(self, context: FeatureContext) -> float | None:
        if len(context.bars) < self.period:
            return None
        close = context.bars[-1].close
        ema = float(talib.EMA(_closes(context), timeperiod=self.period)[-1])
        if not math.isfinite(ema) or ema == 0:
            return None
        return (close - ema) / ema

    def compute_series(
        self, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...], timeframe: Timeframe
    ) -> npt.NDArray[np.float64]:
        closes = _close_array(bars)
        ema = np.asarray(talib.EMA(closes, timeperiod=self.period), dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            return (closes - ema) / ema


@dataclass(frozen=True, slots=True)
class AdxFeature(Feature):
    period: int
    implementation_version: int = 1

    @property
    def output_name(self) -> str:
        return f"adx_{self.period}"

    def required_bars(self, timeframe: Timeframe) -> int:
        return 2 * self.period + 1

    def compute(self, context: FeatureContext) -> float | None:
        if len(context.bars) < self.required_bars(context.timeframe):
            return None
        highs = np.asarray([bar.high for bar in context.bars], dtype=np.float64)
        lows = np.asarray([bar.low for bar in context.bars], dtype=np.float64)
        value = float(talib.ADX(highs, lows, _closes(context), timeperiod=self.period)[-1])
        return value / 100.0 if math.isfinite(value) else None

    def compute_series(
        self, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...], timeframe: Timeframe
    ) -> npt.NDArray[np.float64]:
        highs = np.asarray([bar.high for bar in bars], dtype=np.float64)
        lows = np.asarray([bar.low for bar in bars], dtype=np.float64)
        return (
            np.asarray(
                talib.ADX(highs, lows, _close_array(bars), timeperiod=self.period),
                dtype=np.float64,
            )
            / 100.0
        )


@dataclass(frozen=True, slots=True)
class NormalizedAtrFeature(Feature):
    period: int
    implementation_version: int = 1

    @property
    def output_name(self) -> str:
        return f"atr_normalized_{self.period}"

    def required_bars(self, timeframe: Timeframe) -> int:
        return self.period + 1

    def compute(self, context: FeatureContext) -> float | None:
        if len(context.bars) < self.period + 1:
            return None
        highs = np.asarray([bar.high for bar in context.bars], dtype=np.float64)
        lows = np.asarray([bar.low for bar in context.bars], dtype=np.float64)
        value = float(talib.ATR(highs, lows, _closes(context), timeperiod=self.period)[-1])
        close = context.bars[-1].close
        return value / close if math.isfinite(value) and close > 0 else None

    def compute_series(
        self, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...], timeframe: Timeframe
    ) -> npt.NDArray[np.float64]:
        highs = np.asarray([bar.high for bar in bars], dtype=np.float64)
        lows = np.asarray([bar.low for bar in bars], dtype=np.float64)
        closes = _close_array(bars)
        atr = np.asarray(talib.ATR(highs, lows, closes, timeperiod=self.period), dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            return atr / closes


@dataclass(frozen=True, slots=True)
class RealizedVolatilityFeature(Feature):
    period: int
    implementation_version: int = 1

    @property
    def output_name(self) -> str:
        return f"realized_volatility_{self.period}"

    def required_bars(self, timeframe: Timeframe) -> int:
        return self.period + 1

    def compute(self, context: FeatureContext) -> float | None:
        if len(context.bars) < self.period + 1:
            return None
        closes = _closes(context)[-(self.period + 1) :]
        if np.any(closes <= 0):
            return None
        returns = np.diff(np.log(closes))
        return float(np.std(returns, ddof=1)) if returns.size > 1 else 0.0

    def compute_series(
        self, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...], timeframe: Timeframe
    ) -> npt.NDArray[np.float64]:
        closes = _close_array(bars)
        returns = np.full(len(bars), np.nan, dtype=np.float64)
        valid = (closes[1:] > 0) & (closes[:-1] > 0)
        one_bar = np.full(max(len(bars) - 1, 0), np.nan, dtype=np.float64)
        one_bar[valid] = np.log(closes[1:][valid] / closes[:-1][valid])
        returns[1:] = one_bar
        return (
            pd.Series(returns)
            .rolling(self.period, min_periods=self.period)
            .std()
            .to_numpy(dtype=np.float64)
        )


@dataclass(frozen=True, slots=True)
class NormalizedRsiFeature(Feature):
    period: int
    implementation_version: int = 1

    @property
    def output_name(self) -> str:
        return f"rsi_normalized_{self.period}"

    def required_bars(self, timeframe: Timeframe) -> int:
        return self.period + 1

    def compute(self, context: FeatureContext) -> float | None:
        if len(context.bars) < self.period + 1:
            return None
        value = float(talib.RSI(_closes(context), timeperiod=self.period)[-1])
        return (value - 50.0) / 50.0 if math.isfinite(value) else None

    def compute_series(
        self, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...], timeframe: Timeframe
    ) -> npt.NDArray[np.float64]:
        values = np.asarray(talib.RSI(_close_array(bars), timeperiod=self.period), dtype=np.float64)
        return (values - 50.0) / 50.0


@dataclass(frozen=True, slots=True)
class RangePositionFeature(Feature):
    period: int
    implementation_version: int = 1

    @property
    def output_name(self) -> str:
        return f"range_position_{self.period}"

    def required_bars(self, timeframe: Timeframe) -> int:
        return self.period

    def compute(self, context: FeatureContext) -> float | None:
        if len(context.bars) < self.period:
            return None
        window = context.bars[-self.period :]
        low = min(bar.low for bar in window)
        high = max(bar.high for bar in window)
        if high == low:
            return 0.5
        return (window[-1].close - low) / (high - low)

    def compute_series(
        self, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...], timeframe: Timeframe
    ) -> npt.NDArray[np.float64]:
        lows = pd.Series([bar.low for bar in bars]).rolling(self.period).min()
        highs = pd.Series([bar.high for bar in bars]).rolling(self.period).max()
        closes = pd.Series([bar.close for bar in bars])
        width = highs - lows
        result = ((closes - lows) / width).to_numpy(dtype=np.float64, copy=True)
        complete_flat = (width == 0).to_numpy(dtype=bool)
        result[complete_flat] = 0.5
        return result


@dataclass(frozen=True, slots=True)
class SessionVwapDistanceFeature(Feature):
    implementation_version: int = 1

    @property
    def output_name(self) -> str:
        return "session_vwap_distance"

    def required_bars(self, timeframe: Timeframe) -> int:
        return _bars_per_session(timeframe)

    def compute(self, context: FeatureContext) -> float | None:
        bars = current_session_bars(context.bars)
        total_volume = sum(bar.volume for bar in bars)
        if not bars or total_volume <= 0:
            return None
        vwap = (
            sum(((bar.high + bar.low + bar.close) / 3.0) * bar.volume for bar in bars)
            / total_volume
        )
        return (bars[-1].close - vwap) / vwap if vwap > 0 else None

    def compute_series(
        self, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...], timeframe: Timeframe
    ) -> npt.NDArray[np.float64]:
        result = np.full(len(bars), np.nan, dtype=np.float64)
        current_date = None
        weighted = 0.0
        volume = 0.0
        for index, bar in enumerate(bars):
            local_date = bar.timestamp.astimezone(MARKET_TZ).date()
            if local_date != current_date:
                current_date = local_date
                weighted = 0.0
                volume = 0.0
            if bar.volume < 0:
                continue
            if bar.volume > 0:
                weighted += ((bar.high + bar.low + bar.close) / 3.0) * bar.volume
                volume += bar.volume
            if volume > 0:
                vwap = weighted / volume
                if vwap > 0:
                    result[index] = (bar.close - vwap) / vwap
        return result


@dataclass(frozen=True, slots=True)
class TimeOfDayVolumeZScoreFeature(Feature):
    sessions: int
    implementation_version: int = 1

    @property
    def output_name(self) -> str:
        return f"volume_tod_zscore_{self.sessions}"

    def required_bars(self, timeframe: Timeframe) -> int:
        return (self.sessions + 1) * _bars_per_session(timeframe)

    def compute(self, context: FeatureContext) -> float | None:
        if not context.bars:
            return None
        latest = context.bars[-1]
        latest_local = latest.timestamp.astimezone(MARKET_TZ)
        values: list[float] = []
        seen_dates = set()
        for bar in reversed(context.bars[:-1]):
            local = bar.timestamp.astimezone(MARKET_TZ)
            if local.date() == latest_local.date() or local.date() in seen_dates:
                continue
            if (local.hour, local.minute) != (latest_local.hour, latest_local.minute):
                continue
            if bar.volume < 0:
                return None
            values.append(math.log1p(bar.volume))
            seen_dates.add(local.date())
            if len(values) == self.sessions:
                break
        if len(values) < self.sessions or latest.volume < 0:
            return None
        std = float(np.std(values, ddof=1))
        if std == 0:
            return 0.0
        return (math.log1p(latest.volume) - float(np.mean(values))) / std

    def compute_series(
        self, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...], timeframe: Timeframe
    ) -> npt.NDArray[np.float64]:
        result = np.full(len(bars), np.nan, dtype=np.float64)
        history: defaultdict[tuple[int, int], deque[float]] = defaultdict(
            lambda: deque(maxlen=self.sessions)
        )
        for index, bar in enumerate(bars):
            if bar.volume < 0:
                continue
            local = bar.timestamp.astimezone(MARKET_TZ)
            slot = (local.hour, local.minute)
            previous = history[slot]
            if len(previous) == self.sessions:
                std = float(np.std(previous, ddof=1))
                result[index] = (
                    0.0 if std == 0 else (math.log1p(bar.volume) - float(np.mean(previous))) / std
                )
            previous.append(math.log1p(bar.volume))
        return result


@dataclass(frozen=True, slots=True)
class BenchmarkReturnFeature(Feature):
    period: int
    implementation_version: int = 1

    @property
    def output_name(self) -> str:
        return f"benchmark_return_{self.period}"

    def required_bars(self, timeframe: Timeframe) -> int:
        return self.period + 1

    def compute(self, context: FeatureContext) -> float | None:
        bars = [bar for bar in context.benchmark_bars if bar.timestamp <= context.as_of]
        if len(bars) < self.period + 1 or bars[-1].timestamp != context.as_of:
            return None
        old, new = bars[-1 - self.period].close, bars[-1].close
        if old <= 0 or new <= 0:
            return None
        return math.log(new / old)

    def compute_series(
        self, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...], timeframe: Timeframe
    ) -> npt.NDArray[np.float64]:
        benchmark_closes = _close_array(benchmark_bars)
        benchmark_values = np.full(len(benchmark_bars), np.nan, dtype=np.float64)
        if len(benchmark_bars) > self.period:
            old = benchmark_closes[: -self.period]
            new = benchmark_closes[self.period :]
            valid = (old > 0) & (new > 0)
            values = np.full(len(old), np.nan, dtype=np.float64)
            values[valid] = np.log(new[valid] / old[valid])
            benchmark_values[self.period :] = values
        by_timestamp = {
            bar.timestamp: benchmark_values[index] for index, bar in enumerate(benchmark_bars)
        }
        return np.asarray(
            [by_timestamp.get(bar.timestamp, np.nan) for bar in bars], dtype=np.float64
        )


@dataclass(frozen=True, slots=True)
class TimeOfDayFeature(Feature):
    component: str
    implementation_version: int = 1

    @property
    def output_name(self) -> str:
        return f"time_of_day_{self.component}"

    def required_bars(self, timeframe: Timeframe) -> int:
        return 1

    def compute(self, context: FeatureContext) -> float | None:
        local = context.as_of.astimezone(MARKET_TZ)
        minute = (local.hour * 60 + local.minute) - (9 * 60 + 30)
        phase = 2.0 * math.pi * minute / REGULAR_SESSION_MINUTES
        if self.component == "sin":
            return math.sin(phase)
        if self.component == "cos":
            return math.cos(phase)
        raise ValueError("time_of_day component must be 'sin' or 'cos'")

    def compute_series(
        self, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...], timeframe: Timeframe
    ) -> npt.NDArray[np.float64]:
        phases = np.asarray(
            [
                2.0
                * math.pi
                * (
                    bar.timestamp.astimezone(MARKET_TZ).hour * 60
                    + bar.timestamp.astimezone(MARKET_TZ).minute
                    - (9 * 60 + 30)
                )
                / REGULAR_SESSION_MINUTES
                for bar in bars
            ],
            dtype=np.float64,
        )
        if self.component == "sin":
            return np.sin(phases)
        if self.component == "cos":
            return np.cos(phases)
        raise ValueError("time_of_day component must be 'sin' or 'cos'")
