"""Rule-based risk manager: position sizing, exposure and loss limits.

Rules are applied in order; the first violated rule rejects the signal with
that rule's reason. All thresholds come from RiskConfig (config.yaml).
"""

import math

from carcharoth.config.app_config import RiskConfig
from carcharoth.domain.models import (
    AccountState,
    Quote,
    RiskDecision,
    Signal,
    SignalAction,
)
from carcharoth.interfaces.risk import RiskManager


class BasicRiskManager(RiskManager):
    def __init__(self, config: RiskConfig) -> None:
        self._config = config

    def assess(self, signal: Signal, account: AccountState, quote: Quote) -> RiskDecision:
        if signal.action is SignalAction.HOLD:
            return self._reject(signal, "hold signal, nothing to do")
        if signal.action is SignalAction.SELL:
            return self._assess_sell(signal, account)
        return self._assess_buy(signal, account, quote)

    def _assess_sell(self, signal: Signal, account: AccountState) -> RiskDecision:
        position = account.positions.get(signal.symbol)
        if position is None or position.qty <= 0:
            return self._reject(signal, "no position to sell (shorting not allowed)")
        qty = math.floor(position.qty)
        if qty < 1:
            return self._reject(signal, f"position too small to sell ({position.qty})")
        return RiskDecision(
            signal=signal, approved=True, qty=qty, reason=f"approved: closing {qty} shares"
        )

    def _assess_buy(self, signal: Signal, account: AccountState, quote: Quote) -> RiskDecision:
        config = self._config

        if account.last_equity > 0:
            daily_return = (account.equity - account.last_equity) / account.last_equity
            if daily_return < -config.max_daily_loss_pct:
                return self._reject(
                    signal,
                    f"daily loss limit hit ({daily_return:.2%} < -{config.max_daily_loss_pct:.2%})",
                )

        if signal.symbol in account.positions:
            return self._reject(signal, "position already open (no pyramiding)")

        if len(account.positions) >= config.max_open_positions:
            return self._reject(signal, f"max open positions reached ({config.max_open_positions})")

        if quote.ask_price <= 0:
            return self._reject(signal, "no valid ask price")

        notional = min(
            config.max_position_notional,
            config.max_position_pct_equity * account.equity,
        )
        qty = math.floor(notional / quote.ask_price)
        if qty < 1:
            return self._reject(
                signal, f"price {quote.ask_price:.2f} exceeds position budget {notional:.2f}"
            )

        # Shrink quantity until the (slippage-buffered) cost fits into buying power.
        cost_per_share = quote.ask_price * (1 + config.slippage_buffer)
        available = account.buying_power * config.buying_power_buffer
        if qty * cost_per_share > available:
            qty = math.floor(available / cost_per_share)
        if qty < 1:
            return self._reject(signal, "insufficient buying power")

        exposure = sum(position.market_value for position in account.positions.values())
        max_exposure = config.max_total_exposure_pct * account.equity
        if exposure + qty * quote.ask_price > max_exposure:
            return self._reject(
                signal,
                f"total exposure limit reached ({exposure:.2f} + new > {max_exposure:.2f})",
            )

        return RiskDecision(
            signal=signal,
            approved=True,
            qty=qty,
            reason=f"approved: sized to {qty} shares (~{qty * quote.ask_price:.2f})",
        )

    @staticmethod
    def _reject(signal: Signal, reason: str) -> RiskDecision:
        return RiskDecision(signal=signal, approved=False, qty=0, reason=f"rejected: {reason}")
