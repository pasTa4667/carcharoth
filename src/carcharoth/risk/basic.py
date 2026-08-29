"""Rule-based sizing and exposure controls for explicit trade intents."""

import math

from carcharoth.config.app_config import RiskConfig
from carcharoth.config.directional import DirectionalConfig
from carcharoth.domain.models import (
    AccountState,
    PositionDirection,
    Quote,
    RiskDecision,
    Signal,
    TradeIntent,
)
from carcharoth.interfaces.risk import RiskManager


class BasicRiskManager(RiskManager):
    def __init__(self, config: RiskConfig, directional: DirectionalConfig | None = None) -> None:
        self._config = config
        self._directional = directional or DirectionalConfig()

    def assess(self, signal: Signal, account: AccountState, quote: Quote) -> RiskDecision:
        intent = signal.action
        if intent is TradeIntent.HOLD:
            return self._reject(signal, "hold signal, nothing to do")
        if intent is TradeIntent.CLOSE_LONG:
            return self._assess_close(signal, account, PositionDirection.LONG)
        if intent is TradeIntent.CLOSE_SHORT:
            return self._assess_close(signal, account, PositionDirection.SHORT)
        if intent is TradeIntent.OPEN_SHORT and not self._directional.shorting_enabled:
            return self._reject(signal, "shorting is disabled")
        if intent in (TradeIntent.OPEN_LONG, TradeIntent.OPEN_SHORT):
            return self._assess_open(signal, account, quote)
        return self._reject(signal, f"unsupported intent {intent}")

    def _assess_close(
        self, signal: Signal, account: AccountState, expected: PositionDirection
    ) -> RiskDecision:
        position = account.positions.get(signal.symbol)
        if position is None:
            return self._reject(signal, "no position to close")
        if position.direction is not expected:
            return self._reject(
                signal,
                f"cannot close {expected.value}: position is {position.direction.value}",
            )
        qty = math.floor(position.abs_qty)
        if qty < 1:
            return self._reject(signal, f"position too small to close ({position.qty})")
        return RiskDecision(
            signal=signal,
            approved=True,
            qty=qty,
            reason=f"approved: closing {qty} {expected.value} shares",
        )

    def _assess_open(self, signal: Signal, account: AccountState, quote: Quote) -> RiskDecision:
        config = self._config
        is_short = signal.action is TradeIntent.OPEN_SHORT

        if account.last_equity > 0:
            daily_return = (account.equity - account.last_equity) / account.last_equity
            if daily_return < -config.max_daily_loss_pct:
                return self._reject(
                    signal,
                    f"daily loss limit hit ({daily_return:.2%} < -{config.max_daily_loss_pct:.2%})",
                )
        if signal.symbol in account.positions:
            return self._reject(signal, "position already open (no pyramiding or reversal)")
        if len(account.positions) >= config.max_open_positions:
            return self._reject(signal, f"max open positions reached ({config.max_open_positions})")

        price = quote.bid_price if is_short else quote.ask_price
        if price <= 0:
            return self._reject(signal, f"no valid {'bid' if is_short else 'ask'} price")
        notional = min(
            config.max_position_notional,
            config.max_position_pct_equity * account.equity,
        )
        qty = math.floor(notional / price)
        if qty < 1:
            return self._reject(signal, f"price {price:.2f} exceeds position budget {notional:.2f}")

        buffered_price = price * (
            1 - config.slippage_buffer if is_short else 1 + config.slippage_buffer
        )
        if buffered_price <= 0:
            return self._reject(signal, "slippage buffer leaves no valid execution price")
        available = account.buying_power * config.buying_power_buffer
        if qty * buffered_price > available:
            qty = math.floor(available / buffered_price)
        if qty < 1:
            return self._reject(signal, "insufficient buying power")

        existing_gross = sum(abs(position.market_value) for position in account.positions.values())
        new_notional = qty * price
        max_gross = config.max_total_exposure_pct * account.equity
        if existing_gross + new_notional > max_gross:
            return self._reject(
                signal,
                f"gross exposure limit reached ({existing_gross:.2f} + new > {max_gross:.2f})",
            )
        if is_short:
            existing_short = sum(
                abs(position.market_value)
                for position in account.positions.values()
                if position.qty < 0
            )
            max_short = config.max_short_exposure_pct * account.equity
            if existing_short + new_notional > max_short:
                return self._reject(
                    signal,
                    f"short exposure limit reached ({existing_short:.2f} + new > {max_short:.2f})",
                )

        direction = "short" if is_short else "long"
        return RiskDecision(
            signal=signal,
            approved=True,
            qty=qty,
            reason=f"approved: {direction} {qty} shares (~{new_notional:.2f})",
        )

    @staticmethod
    def _reject(signal: Signal, reason: str) -> RiskDecision:
        return RiskDecision(signal=signal, approved=False, qty=0, reason=f"rejected: {reason}")
