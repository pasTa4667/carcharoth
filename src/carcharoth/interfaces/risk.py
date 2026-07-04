from abc import ABC, abstractmethod

from carcharoth.domain.models import AccountState, Quote, RiskDecision, Signal


class RiskManager(ABC):
    """Applies risk rules and position sizing to strategy signals."""

    @abstractmethod
    def assess(self, signal: Signal, account: AccountState, quote: Quote) -> RiskDecision:
        """Approve or reject a signal and set the order quantity."""
