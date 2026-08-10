"""Monte carlo trade shuffle: resample the baseline run's closed trades.

The strategy runs once; each permutation draws a new trade-P&L sequence from
the baseline round trips and re-evaluates equity/metrics via the backend's
``evaluate_round_trips`` — no re-simulation. Two standard sampling modes:

- ``resample`` (default): draw len(trips) trades **with replacement**
  (bootstrap). Total return, drawdown and profit factor all vary, giving
  confidence bands for the edge itself ("does it rest on a few lucky
  trades?").
- ``shuffle``: reorder the same trades **without replacement**. Order-
  independent metrics (total return, profit factor, win rate) are preserved
  exactly; only path-dependent quantities vary (max drawdown, equity shape) —
  "how lucky was our trade ordering?".

This is a path-risk analysis, not a significance test: the runner reports
distributions/percentiles, never a PASS/FAIL verdict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from carcharoth.interfaces.permutation import PermutationContext, PermutedOutcome

if TYPE_CHECKING:
    import numpy as np


class MonteCarloTradesPermutation:
    """Resample/shuffle baseline round trips, then re-evaluate the equity path."""

    name: ClassVar[str] = "monte_carlo_trades"
    kind: ClassVar[Literal["bars", "trades"]] = "trades"

    def __init__(self, sampling: str = "resample") -> None:
        if sampling not in ("resample", "shuffle"):
            raise ValueError(
                f"unknown sampling {sampling!r}; expected 'resample' or 'shuffle'"
            )
        self.sampling = sampling

    def permute(self, ctx: PermutationContext, rng: np.random.Generator) -> PermutedOutcome:
        if ctx.evaluate_round_trips is None:
            raise ValueError("monte_carlo_trades needs a trade context (evaluate_round_trips)")
        trips = ctx.baseline_round_trips
        n = len(trips)
        if n == 0:
            return ctx.evaluate_round_trips([])
        # shuffle: reorder without replacement; resample: bootstrap with replacement
        order = rng.permutation(n) if self.sampling == "shuffle" else rng.integers(0, n, size=n)
        return ctx.evaluate_round_trips([trips[i] for i in order])
