"""Z-score mean reversion strategy (long-only).

Buys when the current price is far below the rolling mean of recent closes
(z-score <= entry_z) and exits once the price reverts (z-score >= exit_z).

Entries are additionally gated by a trend filter (the rolling mean must be
above a long EMA — the dip is judged against the pre-dip anchor, since the
dipped price itself is below any trend line at exactly the moment a dip
signal fires) and an RSI filter (RSI must confirm selling exhaustion).
Positions carry
an ATR-based stop loss: exit when price drops more than atr_stop_multiplier
ATRs below the average entry price. The strategy is strictly intraday: no
new entries inside the entry cutoff window before the close, and open
positions are flattened inside the flatten window.
"""

from datetime import UTC, datetime

import pandas as pd

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
from carcharoth.strategies import indicators
from carcharoth.strategies.filters import AtrBracket, EndOfDayFilter

_MIN_STD = 1e-9
_LOOKBACK_PADDING = 5


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(
        self,
        lookback: int = 20,
        entry_z: float = -2.0,
        exit_z: float = -0.5,
        trend_ema_period: int = 200,
        rsi_period: int = 14,
        rsi_entry_max: float = 35.0,
        atr_period: int = 14,
        atr_stop_multiplier: float = 2.5,
        timeframe_minutes: int = 5,
        entry_cutoff_minutes: int = 30,
        flatten_minutes: int = 15,
        entry_delay_minutes: int = 0,
    ) -> None:
        if lookback < 2:
            raise ValueError("lookback must be >= 2")
        if timeframe_minutes < 1:
            raise ValueError("timeframe_minutes must be >= 1")
        if exit_z <= entry_z:
            raise ValueError("exit_z must be greater than entry_z")
        if trend_ema_period < 2:
            raise ValueError("trend_ema_period must be >= 2")
        if rsi_period < 2:
            raise ValueError("rsi_period must be >= 2")
        if not 0 < rsi_entry_max < 100:
            raise ValueError("rsi_entry_max must be between 0 and 100")
        if atr_period < 1:
            raise ValueError("atr_period must be >= 1")
        if atr_stop_multiplier <= 0:
            raise ValueError("atr_stop_multiplier must be > 0")
        self._lookback = lookback
        self._entry_z = entry_z
        self._exit_z = exit_z
        self._trend_ema_period = trend_ema_period
        self._rsi_period = rsi_period
        self._rsi_entry_max = rsi_entry_max
        self._timeframe_minutes = timeframe_minutes
        self._bracket = AtrBracket(atr_period, atr_stop_multiplier, take_profit_multiplier=None)
        self._eod = EndOfDayFilter(entry_cutoff_minutes, flatten_minutes, entry_delay_minutes)

    def required_bars(self) -> BarSpec:
        # TA-Lib's EMA is SMA-seeded, so at ~trend_ema_period bars the value is
        # an approximation of the steady-state EMA. Requesting several times the
        # period for warm-up is impractical at intraday resolution; the filter
        # is a coarse regime gate, so this trade-off is accepted.
        lookback = (
            max(
                self._lookback,
                self._trend_ema_period,
                self._rsi_period + 1,
                self._bracket.required_lookback(),
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
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"insufficient history (0/{self._lookback} bars)",
                {},
                now,
            )

        price = quote.mid if quote is not None else bars[-1].close

        # Stop loss and end-of-day flatten come before every history gate: an
        # open position must be exitable even when there are too few bars for
        # the other signals.
        bracket_indicators: dict[str, float] = {}
        if position is not None:
            bracket = self._bracket.check(bars, position.avg_entry_price, price)
            bracket_indicators = bracket.indicators
            if bracket.passed:
                return self._signal(
                    symbol,
                    SignalAction.SELL,
                    bracket.reason,
                    {"price": price, **bracket.indicators},
                    now,
                )
            flatten = self._eod.should_flatten(bars[-1].timestamp)
            if flatten.passed:
                return self._signal(
                    symbol,
                    SignalAction.SELL,
                    flatten.reason,
                    {"price": price, **bracket.indicators, **flatten.indicators},
                    now,
                )

        if len(bars) < self._lookback:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"insufficient history ({len(bars)}/{self._lookback} bars)",
                {},
                now,
            )

        closes = pd.Series([bar.close for bar in bars[-self._lookback :]])
        mean = float(closes.mean())
        std = float(closes.std())

        if std < _MIN_STD:
            return self._signal(
                symbol, SignalAction.HOLD, "flat price series (zero std)", {"price": price}, now
            )

        zscore = (price - mean) / std
        indicator_values = {"zscore": zscore, "mean": mean, "std": std, "price": price}
        indicator_values.update(bracket_indicators)

        if position is not None:
            if zscore >= self._exit_z:
                action = SignalAction.SELL
                reason = f"mean reverted (z={zscore:.2f} >= {self._exit_z}), exiting"
            else:
                action = SignalAction.HOLD
                reason = f"z={zscore:.2f} within thresholds"
            return self._signal(symbol, action, reason, indicator_values, now)

        if zscore > self._entry_z:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"z={zscore:.2f} within thresholds",
                indicator_values,
                now,
            )
        return self._evaluate_entry(symbol, bars, mean, zscore, indicator_values, now)

    def _evaluate_entry(
        self,
        symbol: str,
        bars: list[Bar],
        mean: float,
        zscore: float,
        indicator_values: dict[str, float],
        now: datetime,
    ) -> Signal:
        """Gate a z-score entry signal through the end-of-day cutoff and the
        trend and RSI filters."""
        cutoff = self._eod.blocks_entry(bars[-1].timestamp)
        if cutoff.passed:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"entry blocked: {cutoff.reason}",
                indicator_values | cutoff.indicators,
                now,
            )

        ema_value = indicators.ema(bars, self._trend_ema_period)
        if ema_value is None:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"entry blocked: insufficient history for trend EMA({self._trend_ema_period})",
                indicator_values,
                now,
            )
        indicator_values["trend_ema"] = ema_value
        # Compare the rolling mean, not the dipped price: a -2 sigma dip sits
        # below any trend line by construction, so gating on price would veto
        # exactly the entries the strategy is built to take.
        if mean <= ema_value:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"entry blocked: {self._lookback}-bar mean {mean:.2f} below"
                f" EMA({self._trend_ema_period}) {ema_value:.2f} (downtrend)",
                indicator_values,
                now,
            )

        rsi_value = indicators.rsi(bars, self._rsi_period)
        if rsi_value is None:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"entry blocked: insufficient history for RSI({self._rsi_period})",
                indicator_values,
                now,
            )
        indicator_values["rsi"] = rsi_value
        if rsi_value >= self._rsi_entry_max:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"entry blocked: RSI {rsi_value:.1f} >= {self._rsi_entry_max}",
                indicator_values,
                now,
            )

        return self._signal(
            symbol,
            SignalAction.BUY,
            f"price {zscore:.2f} std devs below {self._lookback}-bar mean,"
            f" uptrend (EMA{self._trend_ema_period}), RSI {rsi_value:.1f}",
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
