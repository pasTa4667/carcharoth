"""Reusable entry/exit components composable into any strategy.

Filters are pure, like strategies: they take bars (or a timestamp) and return
a FilterResult carrying the verdict, a human-readable reason, and the
indicator values they computed, so strategies can merge those into
Signal.indicators without recomputing. What `passed=True` means is defined by
each component (e.g. "entry allowed" for VolumeFilter, "exit now" for
AtrBracket) — the method and class names say which.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from carcharoth.domain.models import Bar
from carcharoth.strategies import indicators, session


@dataclass(frozen=True, slots=True)
class FilterResult:
    passed: bool
    reason: str
    indicators: dict[str, float]


class VolumeFilter:
    """Passes when the latest bar's volume is >= min_ratio x the average
    volume of the preceding `period` bars.

    The current bar is excluded from the average: an in-progress bar would
    otherwise drag its own benchmark down. The flip side is that a partial
    bar's volume under-reads against completed-bar averages (e.g. a daily bar
    early in the session), which blocks entries conservatively.
    """

    def __init__(self, period: int = 20, min_ratio: float = 1.0) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        if min_ratio <= 0:
            raise ValueError("min_ratio must be > 0")
        self._period = period
        self._min_ratio = min_ratio

    def required_lookback(self) -> int:
        """Bars needed for a verdict: the averaging window plus the current bar."""
        return self._period + 1

    def check(self, bars: Sequence[Bar]) -> FilterResult:
        if len(bars) < self._period + 1:
            return FilterResult(
                passed=False,
                reason=f"insufficient history for volume SMA({self._period})",
                indicators={},
            )
        average = indicators.volume_sma(bars[:-1], self._period)
        if average is None or average <= 0:
            return FilterResult(passed=False, reason="no usable volume history", indicators={})
        current = bars[-1].volume
        ratio = current / average
        indicator_values = {"volume": current, "volume_sma": average, "volume_ratio": ratio}
        if ratio >= self._min_ratio:
            return FilterResult(
                passed=True,
                reason=f"volume {ratio:.2f}x the {self._period}-bar average",
                indicators=indicator_values,
            )
        return FilterResult(
            passed=False,
            reason=f"volume {ratio:.2f}x average, below required {self._min_ratio:.2f}x",
            indicators=indicator_values,
        )


class AtrBracket:
    """ATR-scaled exit bracket around an entry price: stop loss at
    entry - stop_multiplier x ATR, optional take profit at
    entry + take_profit_multiplier x ATR. `passed=True` means exit now.

    Levels use the current ATR, recomputed each check, so they breathe with
    volatility instead of being frozen at entry — strategies stay stateless.
    """

    def __init__(
        self,
        atr_period: int = 14,
        stop_multiplier: float = 1.0,
        take_profit_multiplier: float | None = None,
    ) -> None:
        if atr_period < 1:
            raise ValueError("atr_period must be >= 1")
        if stop_multiplier <= 0:
            raise ValueError("stop_multiplier must be > 0")
        if take_profit_multiplier is not None and take_profit_multiplier <= 0:
            raise ValueError("take_profit_multiplier must be > 0")
        self._atr_period = atr_period
        self._stop_multiplier = stop_multiplier
        self._take_profit_multiplier = take_profit_multiplier

    def required_lookback(self) -> int:
        """Bars needed for a verdict: what the ATR needs."""
        return self._atr_period + 1

    def check(self, bars: Sequence[Bar], entry_price: float, price: float) -> FilterResult:
        atr_value = indicators.atr(bars, self._atr_period)
        if atr_value is None:
            return FilterResult(
                passed=False,
                reason=f"insufficient history for ATR({self._atr_period})",
                indicators={},
            )
        stop_price = entry_price - self._stop_multiplier * atr_value
        indicator_values = {"atr": atr_value, "stop_price": stop_price}
        if self._take_profit_multiplier is not None:
            take_profit_price = entry_price + self._take_profit_multiplier * atr_value
            indicator_values["take_profit_price"] = take_profit_price
            if price >= take_profit_price:
                return FilterResult(
                    passed=True,
                    reason=f"take profit: price {price:.2f} >= entry {entry_price:.2f}"
                    f" + {self._take_profit_multiplier} x ATR {atr_value:.2f}",
                    indicators=indicator_values,
                )
        if price < stop_price:
            return FilterResult(
                passed=True,
                reason=f"stop loss: price {price:.2f} < entry {entry_price:.2f}"
                f" - {self._stop_multiplier} x ATR {atr_value:.2f}",
                indicators=indicator_values,
            )
        return FilterResult(passed=False, reason="inside bracket", indicators=indicator_values)


class EndOfDayFilter:
    """Blocks entries and forces exits in the final minutes of the regular
    session so intraday strategies never hold positions overnight.

    Decisions are based on the supplied timestamp — typically the latest
    bar's, which lags real time by up to one bar interval, so pick cutoffs
    with that slack in mind. Early-close days are not modelled (see the
    session module).
    """

    def __init__(self, entry_cutoff_minutes: int = 30, flatten_minutes: int = 15) -> None:
        if flatten_minutes < 0:
            raise ValueError("flatten_minutes must be >= 0")
        if entry_cutoff_minutes < flatten_minutes:
            raise ValueError("entry_cutoff_minutes must be >= flatten_minutes")
        self._entry_cutoff_minutes = entry_cutoff_minutes
        self._flatten_minutes = flatten_minutes

    def blocks_entry(self, ts: datetime) -> FilterResult:
        """`passed=True` when new entries must not be opened at `ts`."""
        remaining = session.minutes_until_close(ts)
        indicator_values = {"minutes_until_close": remaining}
        if remaining <= self._entry_cutoff_minutes:
            return FilterResult(
                passed=True,
                reason=f"{remaining:.0f} min to close, inside"
                f" {self._entry_cutoff_minutes} min entry cutoff",
                indicators=indicator_values,
            )
        return FilterResult(
            passed=False,
            reason=f"{remaining:.0f} min to close",
            indicators=indicator_values,
        )

    def should_flatten(self, ts: datetime) -> FilterResult:
        """`passed=True` when an open position must be closed at `ts`."""
        remaining = session.minutes_until_close(ts)
        indicator_values = {"minutes_until_close": remaining}
        if remaining <= self._flatten_minutes:
            return FilterResult(
                passed=True,
                reason=f"end of day: {remaining:.0f} min to close, flattening"
                f" inside {self._flatten_minutes} min window",
                indicators=indicator_values,
            )
        return FilterResult(
            passed=False,
            reason=f"{remaining:.0f} min to close",
            indicators=indicator_values,
        )
