"""Intraday EMA crossover strategy with VWAP bias (long-only, minute bars).

Buys when the fast EMA has freshly crossed above the slow EMA (within the
last few bars) while the price trades above the session VWAP (bullish
intraday bias). Exits on the first of: take profit or stop loss from the
ATR bracket around the entry price, the end-of-day flatten window, or the
fast EMA crossing back below the slow EMA (trend reversal). The entry
cutoff and flatten windows keep the strategy strictly intraday — no
position survives the session.
"""

import math
from datetime import UTC, datetime

from carcharoth.domain.models import (
    Bar,
    BarSpec,
    Position,
    Quote,
    Signal,
    SignalAction,
    Timeframe,
)
from carcharoth.interfaces.strategy import Strategy
from carcharoth.strategies import indicators, session
from carcharoth.strategies.filters import AtrBracket, EndOfDayFilter

_LOOKBACK_PADDING = 5
_SESSION_MINUTES = 390  # regular NYSE session length


class EmaVwapStrategy(Strategy):
    name = "ema_vwap"

    def __init__(
        self,
        ema_fast: int = 9,
        ema_slow: int = 21,
        cross_within_bars: int = 2,
        atr_period: int = 14,
        atr_stop_multiplier: float = 1.0,
        atr_take_profit_multiplier: float = 2.0,
        timeframe_minutes: int = 5,
        entry_cutoff_minutes: int = 30,
        flatten_minutes: int = 15,
    ) -> None:
        if ema_fast < 2:
            raise ValueError("ema_fast must be >= 2")
        if ema_slow <= ema_fast:
            raise ValueError("ema_slow must be greater than ema_fast")
        if cross_within_bars < 1:
            raise ValueError("cross_within_bars must be >= 1")
        if atr_period < 1:
            raise ValueError("atr_period must be >= 1")
        if atr_stop_multiplier <= 0:
            raise ValueError("atr_stop_multiplier must be > 0")
        if atr_take_profit_multiplier <= 0:
            raise ValueError("atr_take_profit_multiplier must be > 0")
        if timeframe_minutes < 1:
            raise ValueError("timeframe_minutes must be >= 1")
        self._ema_fast = ema_fast
        self._ema_slow = ema_slow
        self._cross_within_bars = cross_within_bars
        self._timeframe_minutes = timeframe_minutes
        self._bracket = AtrBracket(atr_period, atr_stop_multiplier, atr_take_profit_multiplier)
        self._eod = EndOfDayFilter(entry_cutoff_minutes, flatten_minutes)

    def required_bars(self) -> BarSpec:
        # 2x the slow EMA period for the SMA-seeded TA-Lib EMA to converge
        # (same trade-off documented in mean_reversion), plus enough bars to
        # span a full session so the VWAP stays anchored at the open.
        lookback = (
            max(
                self._ema_slow * 2 + self._cross_within_bars,
                self._bracket.required_lookback(),
                math.ceil(_SESSION_MINUTES / self._timeframe_minutes),
            )
            + _LOOKBACK_PADDING
        )
        return BarSpec(Timeframe.minutes(self._timeframe_minutes), lookback)

    def evaluate(
        self,
        symbol: str,
        bars: list[Bar],
        quote: Quote | None,
        position: Position | None,
    ) -> Signal:
        now = datetime.now(UTC)
        if not bars:
            return self._signal(symbol, SignalAction.HOLD, "insufficient history (0 bars)", {}, now)

        price = quote.mid if quote is not None else bars[-1].close

        if position is not None:
            return self._evaluate_exit(symbol, bars, price, position, now)
        return self._evaluate_entry(symbol, bars, price, now)

    def _evaluate_exit(
        self, symbol: str, bars: list[Bar], price: float, position: Position, now: datetime
    ) -> Signal:
        indicator_values: dict[str, float] = {"price": price}

        # Bracket and flatten come before every history gate: an open position
        # must be exitable even when the EMAs cannot be computed yet.
        bracket = self._bracket.check(bars, position.avg_entry_price, price)
        indicator_values.update(bracket.indicators)
        if bracket.passed:
            return self._signal(symbol, SignalAction.SELL, bracket.reason, indicator_values, now)

        flatten = self._eod.should_flatten(bars[-1].timestamp)
        indicator_values.update(flatten.indicators)
        if flatten.passed:
            return self._signal(symbol, SignalAction.SELL, flatten.reason, indicator_values, now)

        fast = indicators.ema(bars, self._ema_fast)
        slow = indicators.ema(bars, self._ema_slow)
        if fast is None or slow is None:
            # Never sell on missing data; the bracket and flatten above are
            # the only exits available with short history.
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"insufficient history for EMA({self._ema_slow})",
                indicator_values,
                now,
            )
        indicator_values.update({"ema_fast": fast, "ema_slow": slow})
        if fast < slow:
            return self._signal(
                symbol,
                SignalAction.SELL,
                f"trend reversed: EMA({self._ema_fast}) {fast:.2f}"
                f" below EMA({self._ema_slow}) {slow:.2f}",
                indicator_values,
                now,
            )
        return self._signal(
            symbol, SignalAction.HOLD, "uptrend intact, inside bracket", indicator_values, now
        )

    def _evaluate_entry(self, symbol: str, bars: list[Bar], price: float, now: datetime) -> Signal:
        indicator_values: dict[str, float] = {"price": price}

        cutoff = self._eod.blocks_entry(bars[-1].timestamp)
        indicator_values.update(cutoff.indicators)
        if cutoff.passed:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"entry blocked: {cutoff.reason}",
                indicator_values,
                now,
            )

        vwap_value = indicators.vwap(session.session_bars(bars))
        if vwap_value is None:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                "entry blocked: no session bars for VWAP",
                indicator_values,
                now,
            )
        indicator_values["vwap"] = vwap_value
        if price <= vwap_value:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"no intraday bias: price {price:.2f} not above VWAP {vwap_value:.2f}",
                indicator_values,
                now,
            )

        # The cross check needs non-NaN slow-EMA values for the current bar
        # plus the cross window behind it.
        min_bars = self._ema_slow + self._cross_within_bars
        if len(bars) < min_bars:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"insufficient history for EMA({self._ema_slow}) cross"
                f" ({len(bars)}/{min_bars} bars)",
                indicator_values,
                now,
            )
        fast_series = indicators.ema_series(bars, self._ema_fast)
        slow_series = indicators.ema_series(bars, self._ema_slow)
        if fast_series is None or slow_series is None:  # unreachable given min_bars
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"insufficient history for EMA({self._ema_slow})",
                indicator_values,
                now,
            )
        fast = float(fast_series[-1])
        slow = float(slow_series[-1])
        indicator_values.update({"ema_fast": fast, "ema_slow": slow})
        if fast <= slow:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"no uptrend: EMA({self._ema_fast}) {fast:.2f}"
                f" not above EMA({self._ema_slow}) {slow:.2f}",
                indicator_values,
                now,
            )
        crossed_recently = any(
            float(fast_series[-1 - i]) <= float(slow_series[-1 - i])
            for i in range(1, self._cross_within_bars + 1)
        )
        if not crossed_recently:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"stale cross: EMA({self._ema_fast}) above EMA({self._ema_slow})"
                f" for more than {self._cross_within_bars} bars",
                indicator_values,
                now,
            )
        return self._signal(
            symbol,
            SignalAction.BUY,
            f"fresh cross: EMA({self._ema_fast}) {fast:.2f} above"
            f" EMA({self._ema_slow}) {slow:.2f} within {self._cross_within_bars} bars,"
            f" price {price:.2f} above VWAP {vwap_value:.2f}",
            indicator_values,
            now,
        )

    def _signal(
        self,
        symbol: str,
        action: SignalAction,
        reason: str,
        indicators: dict[str, float],
        timestamp: datetime,
    ) -> Signal:
        return Signal(
            symbol=symbol,
            action=action,
            strategy=self.name,
            reason=reason,
            indicators=indicators,
            timestamp=timestamp,
        )
