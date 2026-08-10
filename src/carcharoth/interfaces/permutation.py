"""Contracts for permutation testing.

A permutation method answers one question: *given the baseline context and a
seeded RNG, produce one permuted outcome*. The interface deliberately sits one
level above bars-in/bars-out so variants that operate at different pipeline
stages fit the same shape:

- bar-permutation methods (in-sample, walk-forward) transform the bars and
  re-simulate via ``PermutationContext.simulate``;
- trade-shuffle methods skip simulation and recompute equity/metrics from the
  baseline trades.

The context carries only domain-level types plus a backend-supplied
``simulate`` callable, so a future backtest path can build the same context
from its own runner without touching the methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    import numpy as np

    from carcharoth.domain.models import Bar, TradeRecord


@dataclass(frozen=True, slots=True)
class PermutedOutcome:
    """One permutation's score plus small headline metrics (no curves, no
    trades — kept tiny so parallel workers return cheap payloads)."""

    score: float
    #: e.g. total_return, max_drawdown, sharpe, profit_factor, num_trades,
    #: final_equity — whatever the backend's simulate fn reports
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PermutationContext:
    """Everything a permutation method may need for one permuted outcome."""

    #: pre-fetched bars, warm-up included (bars before ``start``)
    bars: Mapping[str, list[Bar]]
    #: simulation window; bars before ``start`` are warm-up and stay untouched
    start: datetime
    end_exclusive: datetime
    #: run one simulation over (permuted) bars and score it (backend-owned)
    simulate: Callable[[Mapping[str, list[Bar]]], PermutedOutcome]
    #: baseline artifacts for post-simulation methods (e.g. trade shuffle)
    baseline_trades: tuple[TradeRecord, ...] = ()
    baseline_score: float | None = None


class PermutationMethod(Protocol):
    """One permutation variant. Implementations are registered in
    ``carcharoth.permutation.registry`` and must be stateless across calls
    (all randomness comes from the passed RNG so runs are reproducible and
    parallelizable)."""

    name: ClassVar[str]

    def permute(self, ctx: PermutationContext, rng: np.random.Generator) -> PermutedOutcome:
        """Produce one permuted outcome from the baseline context."""
        ...
