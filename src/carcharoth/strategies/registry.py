"""Strategy registry: maps config names to strategy factories.

Adding a new strategy: implement the Strategy interface in a new module and
add one entry here. Nothing else in the codebase changes.
"""

from collections.abc import Callable
from typing import Any

from carcharoth.interfaces.strategy import Strategy
from carcharoth.strategies.ema_vwap import EmaVwapStrategy
from carcharoth.strategies.mean_reversion import MeanReversionStrategy

STRATEGIES: dict[str, Callable[..., Strategy]] = {
    MeanReversionStrategy.name: MeanReversionStrategy,
    EmaVwapStrategy.name: EmaVwapStrategy,
}


def build_strategy(name: str, params: dict[str, Any]) -> Strategy:
    try:
        factory = STRATEGIES[name]
    except KeyError:
        available = ", ".join(sorted(STRATEGIES))
        raise ValueError(f"unknown strategy {name!r}; available: {available}") from None
    return factory(**params)
