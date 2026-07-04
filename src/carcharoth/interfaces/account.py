from abc import ABC, abstractmethod

from carcharoth.domain.models import AccountState


class AccountService(ABC):
    """Provides current account state and open positions from the broker."""

    @abstractmethod
    def get_account_state(self) -> AccountState:
        """Return equity, cash, buying power and all open positions.

        Raises AccountError if the broker cannot be reached.
        """
