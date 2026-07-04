from abc import ABC, abstractmethod

from carcharoth.domain.models import Bar, Position, Quote, Signal


class Strategy(ABC):
    """Generates trading signals from market data and current position state.

    Implementations must be pure: no I/O, no mutation of inputs. This keeps
    strategies trivially unit-testable and interchangeable.
    """

    name: str

    @abstractmethod
    def evaluate(
        self,
        symbol: str,
        bars: list[Bar],
        quote: Quote | None,
        position: Position | None,
    ) -> Signal:
        """Return a BUY/SELL/HOLD signal for one symbol."""

    @abstractmethod
    def required_lookback(self) -> int:
        """Number of bars the strategy needs; the engine requests this many."""
