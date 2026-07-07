from abc import ABC, abstractmethod

from carcharoth.domain.models import OrderRequest, OrderResult


class OrderExecutor(ABC):
    """Submits orders to the broker and looks up their status."""

    @abstractmethod
    def submit(self, request: OrderRequest) -> OrderResult:
        """Submit an order. Raises BrokerError on failure."""

    @abstractmethod
    def get_order(self, broker_order_id: str) -> OrderResult:
        """Fetch the current state of an order (used for fill reconciliation)."""

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> None:
        """Request cancellation of an open order (async at the broker).
        Raises BrokerError on failure."""
