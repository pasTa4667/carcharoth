from abc import ABC, abstractmethod

from carcharoth.domain.models import Bar, BarSpec, Position, Quote, Signal


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
        """Return one explicit open/close long/short intent or HOLD.

        Position quantity is signed. Implementations arbitrate entries only
        while flat and manage only the open position's direction otherwise.
        """

    @abstractmethod
    def required_bars(self) -> BarSpec:
        """The bar data (timeframe + lookback) this strategy needs each tick."""
