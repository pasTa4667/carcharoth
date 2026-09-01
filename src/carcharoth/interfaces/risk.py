from abc import ABC, abstractmethod

from carcharoth.domain.models import AccountState, ExposureIntent, Quote, RiskDecision


class RiskManager(ABC):
    """Turns a target exposure into a checked, sized order transition."""

    @abstractmethod
    def assess(self, intent: ExposureIntent, account: AccountState, quote: Quote) -> RiskDecision:
        """Approve/reject one transition and, when approved, set side/quantity."""
