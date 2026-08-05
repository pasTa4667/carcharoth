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
from carcharoth.strategies.session import minutes_since_open

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
        er_period: int = 40,
        er_max: float = 1.0,
        vr_k: int = 4,
        vr_window: int = 30,
        vr_max: float = 2.12,
        min_dip_pct: float = 0.0043,
        stop_z: float = -2.5,
        reversion_exit_z: float = 0.75,
        exit_er_ref: float = 0.3,
        exit_er_slope: float = 0.8,
        exit_z_max: float = 1.3,
        reversal_er_ref: float = 0.55,
        reversal_close_pos: float = 0.5,
        stop_atr_multiplier: float = 6.5,
        require_green_bar: bool = False,
        sharp_drop_lookback: int = 0,
        sharp_drop_pct: float = 0.0,
        min_close_position: float = 0.0,
        macro_trend_ema_period: int = 0,
        deep_dip_bypass: float = 0.0,
        entry_min_since_open: float = 0.0,
        entry_max_since_open: float = 180.0,
        late_min_close_position: float = 0.70,
        dip_vr_ref: float = 1.0,
        dip_vr_slope: float = 0.0015,
        dip_er_ref: float = 0.3,
        dip_er_slope: float = 0.008,
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
        # Stop width is a structural choice: mean-reversion needs room to work,
        # so the stop is a *floor* (never tighter than stop_atr_multiplier ATRs)
        # regardless of the config-passed atr_stop_multiplier. A too-tight stop
        # cuts positions that would have reverted (empirically win_rate collapses).
        effective_atr_mult = max(atr_stop_multiplier, stop_atr_multiplier)
        self._bracket = AtrBracket(atr_period, effective_atr_mult, take_profit_multiplier=None)
        self._eod = EndOfDayFilter(entry_cutoff_minutes, flatten_minutes, entry_delay_minutes)
        # Adaptive reversion exit: deep dips (min_dip) revert further than the
        # shallow exit_z target, so hold until the price has meaningfully crossed
        # back through the mean. Effective exit = the more patient of the two.
        self._reversion_exit_z = max(exit_z, reversion_exit_z)
        self._exit_er_ref = exit_er_ref
        self._exit_er_slope = exit_er_slope
        self._exit_z_max = exit_z_max
        self._reversal_er_ref = reversal_er_ref
        self._reversal_close_pos = reversal_close_pos
        self._er_period = er_period
        self._er_max = er_max
        self._vr_k = vr_k
        self._vr_window = vr_window
        self._vr_max = vr_max
        self._min_dip_pct = min_dip_pct
        self._stop_z = stop_z
        self._require_green_bar = require_green_bar
        self._sharp_drop_lookback = sharp_drop_lookback
        self._sharp_drop_pct = sharp_drop_pct
        self._min_close_position = min_close_position
        self._macro_trend_ema_period = macro_trend_ema_period
        self._deep_dip_bypass = deep_dip_bypass
        self._entry_min_since_open = entry_min_since_open
        self._entry_max_since_open = entry_max_since_open
        self._late_min_close_position = late_min_close_position
        self._dip_vr_ref = dip_vr_ref
        self._dip_vr_slope = dip_vr_slope
        self._dip_er_ref = dip_er_ref
        self._dip_er_slope = dip_er_slope

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
                self._er_period + 1,
                self._vr_window + 1,
            )
            + _LOOKBACK_PADDING
        )
        lookback = max(lookback, self._macro_trend_ema_period + _LOOKBACK_PADDING)
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
            # Regime-adaptive exit target: choppy regimes (low ER) revert fast
            # and give back quickly, so take the quick exit; trending regimes
            # (high ER) let a reversion run further, so hold for a deeper target.
            # h1 (choppy) and h2 (trending) empirically want opposite targets;
            # scaling the target to ER captures both instead of a compromise.
            exit_target = self._reversion_exit_z
            if self._exit_er_slope > 0:
                er_exit = indicators.efficiency_ratio(bars, self._er_period)
                if er_exit is not None:
                    exit_target = min(
                        self._exit_z_max,
                        self._reversion_exit_z
                        + self._exit_er_slope * max(0.0, er_exit - self._exit_er_ref),
                    )
            if zscore >= exit_target:
                action = SignalAction.SELL
                reason = f"mean reverted (z={zscore:.2f} >= {exit_target:.2f}), exiting"
            elif zscore <= self._stop_z:
                action = SignalAction.SELL
                reason = f"z-stop hit (z={zscore:.2f} <= {self._stop_z}), exiting"
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

        # Time-of-day entry window: the mean-reversion edge lives in the opening
        # hours (dips revert intraday); late-session dips tend to be trend
        # continuation. Entries before entry_min are always blocked. Entries
        # after entry_max are blocked UNLESS late_min_close_position is set, in
        # which case they are admitted only with a strong reversal confirmation.
        is_late = False
        if self._entry_min_since_open > 0 or self._entry_max_since_open > 0:
            mso = minutes_since_open(bars[-1].timestamp)
            indicator_values["min_since_open"] = mso
            if self._entry_min_since_open > 0 and mso < self._entry_min_since_open:
                return self._signal(
                    symbol,
                    SignalAction.HOLD,
                    f"entry blocked: {mso:.0f}min since open (too early)",
                    indicator_values,
                    now,
                )
            if self._entry_max_since_open > 0 and mso > self._entry_max_since_open:
                if self._late_min_close_position <= 0:
                    return self._signal(
                        symbol,
                        SignalAction.HOLD,
                        f"entry blocked: {mso:.0f}min since open (too late)",
                        indicator_values,
                        now,
                    )
                is_late = True

        # Minimum dislocation depth (0 = disabled): deep dips revert, shallow don't.
        # The depth requirement is *regime-adaptive*: in more-trending regimes
        # (higher variance ratio) a shallow dip is likely trend-continuation, so
        # require a progressively deeper dislocation. In strongly mean-reverting
        # regimes (VR near/below dip_vr_ref) the base depth suffices. This is a
        # smooth structural version of the binary VR gate applied to depth.
        price = indicator_values["price"]
        dip_pct = (mean - price) / mean if mean > 0 else 0.0
        indicator_values["dip_pct"] = dip_pct
        er_now = indicators.efficiency_ratio(bars, self._er_period)
        effective_min_dip = self._min_dip_pct
        if self._dip_vr_slope > 0:
            vr_depth = indicators.variance_ratio(bars, self._vr_k, self._vr_window)
            if vr_depth is not None:
                effective_min_dip += self._dip_vr_slope * max(0.0, vr_depth - self._dip_vr_ref)
        # Kaufman efficiency-ratio adds an orthogonal trend signal: ER measures
        # directional efficiency (how much of the path is net displacement).
        # High ER = strong trend, so require a deeper dip. VR and ER capture
        # different facets of trending (autocorrelation vs directionality).
        if self._dip_er_slope > 0 and er_now is not None:
            effective_min_dip += self._dip_er_slope * max(0.0, er_now - self._dip_er_ref)
        indicator_values["effective_min_dip"] = effective_min_dip
        if effective_min_dip > 0 and dip_pct < effective_min_dip:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                f"entry blocked: dip {dip_pct:.4f} < min_dip {effective_min_dip:.4f}",
                indicator_values,
                now,
            )

        # Regime-conditional reversal confirmation: in trending regimes (high ER)
        # a falling dip is more likely trend-continuation, so require the last
        # bar to close in the upper part of its range (the bounce has begun).
        # Choppy regimes (low ER) skip this -- their dips revert regardless.
        if (
            self._reversal_close_pos > 0
            and er_now is not None
            and er_now > self._reversal_er_ref
            and len(bars) >= 1
        ):
            last = bars[-1]
            rng = last.high - last.low
            if rng > 0:
                cpos = (last.close - last.low) / rng
                indicator_values["reversal_close_pos"] = cpos
                if cpos < self._reversal_close_pos:
                    return self._signal(
                        symbol,
                        SignalAction.HOLD,
                        f"entry blocked: trending-regime reversal cpos {cpos:.2f}"
                        f" < {self._reversal_close_pos}",
                        indicator_values,
                        now,
                    )

        # Kaufman efficiency-ratio regime gate (er_max>=1 disables it).
        if self._er_max < 1.0:
            er_value = indicators.efficiency_ratio(bars, self._er_period)
            if er_value is not None:
                indicator_values["efficiency_ratio"] = er_value
                if er_value > self._er_max:
                    return self._signal(
                        symbol,
                        SignalAction.HOLD,
                        f"entry blocked: ER {er_value:.3f} > {self._er_max} (trending)",
                        indicator_values,
                        now,
                    )

        # Lo-MacKinlay variance-ratio regime gate (vr_max>=10 disables it).
        if self._vr_max < 10.0:
            vr_value = indicators.variance_ratio(bars, self._vr_k, self._vr_window)
            if vr_value is not None:
                indicator_values["variance_ratio"] = vr_value
                if vr_value > self._vr_max:
                    return self._signal(
                        symbol,
                        SignalAction.HOLD,
                        f"entry blocked: VR {vr_value:.3f} > {self._vr_max}",
                        indicator_values,
                        now,
                    )

        # Reversal-confirmation: the most recent bar must be green (close up on
        # the prior close), i.e. the bounce has begun -- avoids catching a knife
        # still falling.
        if self._require_green_bar and len(bars) >= 2 and bars[-1].close <= bars[-2].close:
            return self._signal(
                symbol,
                SignalAction.HOLD,
                "entry blocked: last bar not green (no reversal yet)",
                indicator_values,
                now,
            )

        # Softer reversal confirmation: the last bar's close must sit in the
        # upper `min_close_position` fraction of its own high-low range (i.e.
        # buyers stepped in intrabar). Fires far more often than a full green
        # bar while still rejecting closes pinned at the lows (knife-catching).
        # Reversal-confirmation requirement (close in upper part of bar range).
        # Deep opening dips (>= deep_dip_bypass) are already high-quality and
        # skip it; shallow dips need `min_close_position`; late-session dips need
        # the stricter `late_min_close_position`.
        deep_enough = self._deep_dip_bypass > 0 and dip_pct >= self._deep_dip_bypass
        required_close_pos = 0.0
        if is_late:
            required_close_pos = self._late_min_close_position
        elif self._min_close_position > 0 and not deep_enough:
            required_close_pos = self._min_close_position
        if required_close_pos > 0 and len(bars) >= 1:
            last = bars[-1]
            rng = last.high - last.low
            if rng > 0:
                close_pos = (last.close - last.low) / rng
                indicator_values["close_pos"] = close_pos
                if close_pos < required_close_pos:
                    return self._signal(
                        symbol,
                        SignalAction.HOLD,
                        f"entry blocked: close_pos {close_pos:.2f} < {required_close_pos}",
                        indicator_values,
                        now,
                    )

        # Sharp-overshoot gate: require the dip to include a fast recent drop
        # (a liquidity overshoot that mechanically reverts) rather than a slow
        # directional grind (trend). Measured as the drop from the highest close
        # in the last `sharp_drop_lookback` bars to the current price.
        if self._sharp_drop_lookback > 0 and self._sharp_drop_pct > 0:
            window = bars[-self._sharp_drop_lookback:]
            recent_high = max(b.close for b in window)
            drop = (recent_high - price) / recent_high if recent_high > 0 else 0.0
            indicator_values["recent_drop"] = drop
            if drop < self._sharp_drop_pct:
                return self._signal(
                    symbol,
                    SignalAction.HOLD,
                    f"entry blocked: recent drop {drop:.4f} < {self._sharp_drop_pct}",
                    indicator_values,
                    now,
                )

        # Macro-trend gate: reject dips inside a sustained multi-day downtrend.
        # The intraday trend-EMA (~120 bars) does not see multi-day drift, so
        # momentum names bleeding lower keep producing dip signals that never
        # revert. Require price above a much longer EMA.
        if self._macro_trend_ema_period > 0:
            macro_ema = indicators.ema(bars, self._macro_trend_ema_period)
            if macro_ema is not None:
                indicator_values["macro_ema"] = macro_ema
                # Compare the de-noised rolling mean, not the dipped price: a dip
                # momentarily below the long EMA is fine; a whole-mean below it
                # is a confirmed multi-day downtrend.
                if mean < macro_ema:
                    return self._signal(
                        symbol,
                        SignalAction.HOLD,
                        f"entry blocked: price {price:.2f} < macro EMA"
                        f"({self._macro_trend_ema_period}) {macro_ema:.2f} (downtrend)",
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

        # Require the z-score to be improving (turning back toward the mean)
        # before entry. Computes the previous bar's z-score against its own
        # rolling window so the comparison accounts for shifts in mean/std.
        if len(bars) >= self._lookback + 1:
            prev_closes = pd.Series([bar.close for bar in bars[-self._lookback - 1 : -1]])
            prev_std = float(prev_closes.std())
            if prev_std >= _MIN_STD:
                prev_mean = float(prev_closes.mean())
                prev_zscore = (bars[-2].close - prev_mean) / prev_std
                indicator_values["prev_zscore"] = prev_zscore
                if zscore <= prev_zscore:
                    return self._signal(
                        symbol,
                        SignalAction.HOLD,
                        f"entry blocked: z-score not improving"
                        f" (z={zscore:.2f} <= prev_z={prev_zscore:.2f})",
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
