"""Contracts for permutation testing.

A permutation method answers one question: *given the baseline context and a
seeded RNG, produce one permuted outcome*. The interface deliberately sits one
level above bars-in/bars-out so variants that operate at different pipeline
stages fit the same shape:

- bar-permutation methods (``kind == "bars"``: in-sample, walk-forward)
  transform the bars and re-simulate via ``PermutationContext.simulate``;
- trade-shuffle methods (``kind == "trades"``: monte carlo) skip simulation
  and recompute equity/metrics from the baseline round trips via
  ``PermutationContext.evaluate_round_trips``.

The context carries only domain-level types plus backend-supplied callables
(``simulate``, ``evaluate_round_trips``), so the backtest path builds the same
context from its own artifacts without touching the methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

    import numpy as np

    from carcharoth.analysis.metrics import RoundTrip
    from carcharoth.domain.models import Bar


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
    """Everything a permutation method may need for one permuted outcome.

    Bar methods use ``bars``/``start``/``end_exclusive``/``simulate``; trade
    methods use ``baseline_round_trips``/``evaluate_round_trips``. Each
    backend fills only the seam its methods need.
    """

    #: pre-fetched bars, warm-up included (bars before ``start``)
    bars: Mapping[str, list[Bar]] = field(default_factory=dict)
    #: simulation window; bars before ``start`` are warm-up and stay untouched
    start: datetime | None = None
    end_exclusive: datetime | None = None
    #: run one simulation over (permuted) bars and score it (backend-owned)
    simulate: Callable[[Mapping[str, list[Bar]]], PermutedOutcome] | None = None
    #: baseline closed trades for post-simulation methods (trade shuffle)
    baseline_round_trips: tuple[RoundTrip, ...] = ()
    #: rebuild equity + metrics from a sampled trade sequence (backend-owned)
    evaluate_round_trips: Callable[[Sequence[RoundTrip]], PermutedOutcome] | None = None


class PermutationMethod(Protocol):
    """One permutation variant. Implementations are registered in
    ``carcharoth.permutation.registry`` and must be stateless across calls
    (all randomness comes from the passed RNG so runs are reproducible and
    parallelizable)."""

    name: ClassVar[str]
    #: which context seam the method drives: "bars" re-simulates permuted
    #: bars, "trades" re-evaluates sampled baseline round trips
    kind: ClassVar[Literal["bars", "trades"]]

    def permute(self, ctx: PermutationContext, rng: np.random.Generator) -> PermutedOutcome:
        """Produce one permuted outcome from the baseline context."""
        ...
