"""In-sample bar permutation (Masters-style market-data permutation).

Per symbol, the in-window bars are decomposed in log space into
close-to-open gaps and intrabar (open→high/low/close) relatives. The gaps and
the intrabar tuples are shuffled with two independent permutations, then bars
are rebuilt sequentially from the last pre-window close. This destroys the
serial structure a real edge would exploit while preserving the return
distribution, overall drift, and per-bar OHLC shape (high ≥ open/close ≥ low
survives because each intrabar tuple moves as one unit; volume travels with
its intrabar tuple).

Warm-up bars before the simulation window stay untouched so the strategy
warms up on real data, exactly like the baseline run.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from typing import TYPE_CHECKING, ClassVar, Literal

from carcharoth.domain.models import Bar
from carcharoth.interfaces.permutation import PermutationContext, PermutedOutcome

if TYPE_CHECKING:
    from datetime import datetime

    import numpy as np


class InSampleBarsPermutation:
    """Shuffle in-window bar returns per symbol, then re-simulate."""

    name: ClassVar[str] = "in_sample_bars"
    kind: ClassVar[Literal["bars", "trades"]] = "bars"

    def permute(self, ctx: PermutationContext, rng: np.random.Generator) -> PermutedOutcome:
        if ctx.simulate is None or ctx.start is None:
            raise ValueError("in_sample_bars needs a bars context (simulate + start)")
        permuted = {
            symbol: permute_symbol_bars(bars, ctx.start, rng)
            for symbol, bars in ctx.bars.items()
        }
        return ctx.simulate(permuted)


def permute_symbol_bars(bars: list[Bar], start: datetime, rng: np.random.Generator) -> list[Bar]:
    """One shuffled copy of a symbol's bars; bars before ``start`` unchanged.

    Exposed as a function (not just via the class) so tests can assert the
    OHLC invariants directly.
    """
    split = bisect_left([bar.timestamp for bar in bars], start)
    warmup, window = bars[:split], bars[split:]
    if len(window) < 2:
        return bars

    anchor_close = warmup[-1].close if warmup else window[0].open
    gaps: list[float] = []
    intrabars: list[tuple[float, float, float, float]] = []  # (rel high/low/close, volume)
    prev_close = anchor_close
    for bar in window:
        gaps.append(_log_ratio(bar.open, prev_close))
        intrabars.append(
            (
                _log_ratio(bar.high, bar.open),
                _log_ratio(bar.low, bar.open),
                _log_ratio(bar.close, bar.open),
                bar.volume,
            )
        )
        prev_close = bar.close

    gap_order = rng.permutation(len(gaps))
    intrabar_order = rng.permutation(len(intrabars))

    permuted: list[Bar] = []
    prev_close = anchor_close
    for position, original in enumerate(window):
        open_ = prev_close * math.exp(gaps[gap_order[position]])
        rel_high, rel_low, rel_close, volume = intrabars[intrabar_order[position]]
        close = open_ * math.exp(rel_close)
        permuted.append(
            Bar(
                symbol=original.symbol,
                timestamp=original.timestamp,
                open=open_,
                high=open_ * math.exp(rel_high),
                low=open_ * math.exp(rel_low),
                close=close,
                volume=volume,
            )
        )
        prev_close = close
    return warmup + permuted


def _log_ratio(price: float, reference: float) -> float:
    if price <= 0 or reference <= 0:  # defensive: bad data yields a flat bar
        return 0.0
    return math.log(price / reference)
