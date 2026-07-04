"""Z-score mean reversion strategy (long-only).

Buys when the current price is far below the rolling mean of recent closes
(z-score <= entry_z) and exits once the price reverts (z-score >= exit_z).
"""

from datetime import UTC, datetime

import pandas as pd

from carcharoth.domain.models import Bar, Position, Quote, Signal, SignalAction
from carcharoth.interfaces.strategy import Strategy

_MIN_STD = 1e-9
_LOOKBACK_PADDING = 5


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(
        self,
        lookback: int = 20,
        entry_z: float = -2.0,
        exit_z: float = -0.5,
    ) -> None:
        if lookback < 2:
            raise ValueError("lookback must be >= 2")
        if exit_z <= entry_z:
            raise ValueError("exit_z must be greater than entry_z")
        self._lookback = lookback
        self._entry_z = entry_z
        self._exit_z = exit_z

    def required_lookback(self) -> int:
        return self._lookback + _LOOKBACK_PADDING

    def evaluate(
        self,
        symbol: str,
        bars: list[Bar],
        quote: Quote | None,
        position: Position | None,
    ) -> Signal:
        now = datetime.now(UTC)
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
        price = quote.mid if quote is not None else float(closes.iloc[-1])

        if std < _MIN_STD:
            return self._signal(
                symbol, SignalAction.HOLD, "flat price series (zero std)", {"price": price}, now
            )

        zscore = (price - mean) / std
        indicators = {"zscore": zscore, "mean": mean, "std": std, "price": price}

        if position is None and zscore <= self._entry_z:
            action = SignalAction.BUY
            reason = f"price {zscore:.2f} std devs below {self._lookback}-bar mean"
        elif position is not None and zscore >= self._exit_z:
            action = SignalAction.SELL
            reason = f"mean reverted (z={zscore:.2f} >= {self._exit_z}), exiting"
        else:
            action = SignalAction.HOLD
            reason = f"z={zscore:.2f} within thresholds"

        return self._signal(symbol, action, reason, indicators, now)

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
