"""In-memory result store for quick-test runs — plain data, no I/O.

Keeping the result a plain dataclass over domain types makes future
permutation testing trivial: trade permutation re-runs the (pure) metric
functions over a shuffled copy; market-data permutation transforms the bars
dict before simulation.
"""

from dataclasses import dataclass, field

from carcharoth.domain.models import EquityPoint, PositionSnapshot, TradeRecord


@dataclass(slots=True)
class SymbolResult:
    """Everything one independent symbol simulation produced."""

    symbol: str
    trades: list[TradeRecord] = field(default_factory=list)
    equity: list[EquityPoint] = field(default_factory=list)
    #: cash component of the equity curve (same timestamps as `equity`)
    cash: list[EquityPoint] = field(default_factory=list)
    #: bar-close unrealized P&L while a position was open (for MAE/MFE)
    snapshots: list[PositionSnapshot] = field(default_factory=list)


@dataclass(slots=True)
class QuickTestResult:
    """All per-symbol simulations of one quick-test run."""

    #: starting capital of each independent symbol portfolio
    capital_per_symbol: float
    symbols: dict[str, SymbolResult] = field(default_factory=dict)

    @property
    def trades(self) -> list[TradeRecord]:
        """All fills across symbols, oldest first."""
        return sorted(
            (t for r in self.symbols.values() for t in r.trades), key=lambda t: t.timestamp
        )

    @property
    def snapshots(self) -> list[PositionSnapshot]:
        return [s for r in self.symbols.values() for s in r.snapshots]

    def aggregate_equity(self) -> list[EquityPoint]:
        """Sum of the independent per-symbol equity curves on the union
        timestamp grid, carrying each symbol's last value forward. Before a
        symbol's first point its starting capital is used, so the aggregate
        starts at ``capital_per_symbol * len(symbols)``."""
        return _sum_curves([r.equity for r in self.symbols.values()], self.capital_per_symbol)

    def aggregate_cash(self) -> list[EquityPoint]:
        """Sum of the per-symbol cash curves (see ``aggregate_equity``)."""
        return _sum_curves([r.cash for r in self.symbols.values()], self.capital_per_symbol)


def _sum_curves(curves: list[list[EquityPoint]], base: float) -> list[EquityPoint]:
    curves = [curve for curve in curves if curve]
    if not curves:
        return []
    grid = sorted({point.timestamp for curve in curves for point in curve})
    result: list[EquityPoint] = []
    indices = [0] * len(curves)
    lasts = [base] * len(curves)
    for ts in grid:
        for i, curve in enumerate(curves):
            while indices[i] < len(curve) and curve[indices[i]].timestamp <= ts:
                lasts[i] = curve[indices[i]].equity
                indices[i] += 1
        result.append(EquityPoint(timestamp=ts, equity=sum(lasts)))
    return result
