"""Rule-based symmetric long/short risk manager."""

import math

from carcharoth.config.app_config import RiskConfig
from carcharoth.domain.models import (
    AccountState,
    ExposureIntent,
    Quote,
    RiskDecision,
    Side,
    TargetExposure,
)
from carcharoth.interfaces.risk import RiskManager


class BasicRiskManager(RiskManager):
    def __init__(self, config: RiskConfig) -> None:
        self._config = config

    def assess(self, intent: ExposureIntent, account: AccountState, quote: Quote) -> RiskDecision:
        position = account.positions.get(intent.symbol)
        current = position.exposure if position is not None else TargetExposure.FLAT
        target = intent.target
        if target is TargetExposure.UNCHANGED:
            return self._reject(intent, "unchanged target")
        if target is current:
            return self._reject(intent, f"already {current.value} (no pyramiding)")

        # Any transition away from an open position closes it first. Risk-
        # reducing closes bypass entry lockouts and sizing limits.
        if position is not None:
            qty = abs(position.qty)
            if qty <= 0:
                return self._reject(intent, "position has zero quantity")
            side = Side.SELL if position.qty > 0 else Side.BUY
            suffix = " before reversal" if target is not TargetExposure.FLAT else ""
            return RiskDecision(
                intent=intent,
                approved=True,
                qty=qty,
                side=side,
                reason=f"approved: closing {current.value}{suffix}",
            )

        if target is TargetExposure.FLAT:
            return self._reject(intent, "already flat")
        return self._assess_entry(intent, target, account, quote)

    def _assess_entry(
        self,
        intent: ExposureIntent,
        target: TargetExposure,
        account: AccountState,
        quote: Quote,
    ) -> RiskDecision:
        config = self._config
        if account.last_equity > 0:
            daily_return = (account.equity - account.last_equity) / account.last_equity
            if daily_return < -config.max_daily_loss_pct:
                return self._reject(
                    intent,
                    f"daily loss limit hit ({daily_return:.2%} < -{config.max_daily_loss_pct:.2%})",
                )
        if len(account.positions) >= config.max_open_positions:
            return self._reject(intent, f"max open positions reached ({config.max_open_positions})")

        side = Side.BUY if target is TargetExposure.LONG else Side.SELL
        price = quote.ask_price if side is Side.BUY else quote.bid_price
        if price <= 0:
            return self._reject(intent, f"no valid {side.value} price")
        notional = min(
            config.max_position_notional,
            config.max_position_pct_equity * account.equity,
        )
        qty = math.floor(notional / price)
        if qty < 1:
            return self._reject(intent, f"price {price:.2f} exceeds position budget {notional:.2f}")

        buffered_notional = qty * price * (1 + config.slippage_buffer)
        available = account.buying_power * config.buying_power_buffer
        if buffered_notional > available:
            qty = math.floor(available / (price * (1 + config.slippage_buffer)))
        if qty < 1:
            return self._reject(intent, "insufficient buying power")

        gross_exposure = sum(position.gross_market_value for position in account.positions.values())
        max_exposure = config.max_total_exposure_pct * account.equity
        if gross_exposure + qty * price > max_exposure:
            return self._reject(
                intent,
                f"gross exposure limit reached ({gross_exposure:.2f} + new > {max_exposure:.2f})",
            )
        return RiskDecision(
            intent=intent,
            approved=True,
            qty=float(qty),
            side=side,
            reason=f"approved: open {target.value} {qty} shares (~{qty * price:.2f})",
        )

    @staticmethod
    def _reject(intent: ExposureIntent, reason: str) -> RiskDecision:
        return RiskDecision(
            intent=intent,
            approved=False,
            qty=0,
            side=None,
            reason=f"rejected: {reason}",
        )
