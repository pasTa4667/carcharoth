"""Strategy registry: maps config names to strategy factories.

Adding a new strategy: implement the Strategy interface in a new module and
add one entry here. Nothing else in the codebase changes.
"""

from collections.abc import Callable
from typing import Any, Protocol

from carcharoth.interfaces.strategy import Strategy
from carcharoth.strategies.ema_vwap import EmaVwapStrategy
from carcharoth.strategies.mean_reversion import MeanReversionStrategy


class DirectionalPolicy(Protocol):
    shorting_enabled: bool
    min_entry_score: float
    min_score_margin: float


STRATEGIES: dict[str, Callable[..., Strategy]] = {
    MeanReversionStrategy.name: MeanReversionStrategy,
    EmaVwapStrategy.name: EmaVwapStrategy,
}


def build_strategy(
    name: str, params: dict[str, Any], directional: DirectionalPolicy | None = None
) -> Strategy:
    try:
        factory = STRATEGIES[name]
    except KeyError:
        available = ", ".join(sorted(STRATEGIES))
        raise ValueError(f"unknown strategy {name!r}; available: {available}") from None
    return factory(
        **params,
        shorting_enabled=directional.shorting_enabled if directional else False,
        min_entry_score=directional.min_entry_score if directional else 0.5,
        min_score_margin=directional.min_score_margin if directional else 0.1,
    )
