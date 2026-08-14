"""The root config model: one validated schema for every run type.

``RunConfig`` composes the shared sections (symbols, date window, engine,
strategies, regime, risk, objectives, cache) with the run-type-specific
sections (``backtest``, ``quicktest``, ``optimization``). Strategy params
are defined exactly once in the ``strategies`` map; the quicktest and
optimizer only *reference* them, so a quicktest and a backtest of the same
strategy are guaranteed to use identical params unless a layer explicitly
overrides them.

The legacy per-run-type models (``QuickTestConfig``, ``OptimizeConfig``)
stay as the validated shapes their runners consume; ``quicktest_view()`` /
``optimize_view()`` derive them from the resolved root config.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import Field, model_validator

from carcharoth.config.app_config import (
    BacktestConfig,
    CacheConfig,
    EngineConfig,
    ObjectiveConfig,
    RegimeConfig,
    RiskConfig,
    StrategyConfig,
)
from carcharoth.config.optimize_config import (
    BacktestWindowConfig,
    ConstraintConfig,
    OptimizeConfig,
    SearchParam,
    StudyConfig,
)
from carcharoth.config.quicktest_config import (
    PermutationConfig,
    QuickTestConfig,
    QuickTestStrategyConfig,
)
from carcharoth.config.strict import StrictModel


class DataConfig(StrictModel):
    """The shared historical window: backtests, quicktests and optimizer
    trials all replay this range. Ignored by live paper trading."""

    start: date
    #: inclusive, like the backtest CLI's --end
    end: date

    @model_validator(mode="after")
    def _start_before_end(self) -> "DataConfig":
        if self.end < self.start:
            raise ValueError("data.end must not be before data.start")
        return self

    @property
    def start_dt(self) -> datetime:
        return datetime(self.start.year, self.start.month, self.start.day, tzinfo=UTC)

    @property
    def end_exclusive_dt(self) -> datetime:
        return datetime(self.end.year, self.end.month, self.end.day, tzinfo=UTC) + timedelta(days=1)


class QuickTestSectionConfig(StrictModel):
    """Quicktest knobs. The strategy is a *name* into the shared
    ``strategies`` map — params are never duplicated here."""

    #: name in ``strategies``; None falls back to the single active strategy
    strategy: str | None = None
    #: starting capital per symbol (each symbol is simulated independently)
    capital: float = Field(default=10_000.0, gt=0)
    #: buy notional as a fraction of ``capital``
    position_size_pct: float = Field(default=0.10, gt=0, le=1)
    #: synthetic quote spread; 0 = frictionless
    spread_pct: float = Field(default=0.0, ge=0)
    #: fills execute this fraction worse than the quoted side; 0 = frictionless
    slippage_pct: float = Field(default=0.0, ge=0)
    #: named objective from ``objectives:`` used for fitness
    objective: str = "default"
    #: permutation-test settings; only consulted when ``--permute`` is given
    permutation: PermutationConfig | None = None


class OptimizationConfig(StrictModel):
    """Optuna study settings. ``search_space`` is *atomic* in the layer
    system: a layer that defines it replaces it wholesale (its keys are
    study-specific dot-paths, not fixed structure)."""

    study: StudyConfig | None = None
    #: name of an objective defined in ``objectives:``
    objective: str = "default"
    constraints: list[ConstraintConfig] = Field(default_factory=list)
    #: dot-path into the resolved config -> parameter distribution
    search_space: dict[str, SearchParam] = Field(default_factory=dict)


class RunConfig(StrictModel):
    #: the single source of truth for the symbol universe (all run types)
    symbols: list[str] = Field(min_length=1)
    data: DataConfig
    engine: EngineConfig = EngineConfig()
    #: strategies keyed by strategy name; each carries its params once
    strategies: dict[str, StrategyConfig] = Field(min_length=1)
    regime: RegimeConfig | None = None
    risk: RiskConfig = RiskConfig()
    backtest: BacktestConfig = BacktestConfig()
    quicktest: QuickTestSectionConfig = QuickTestSectionConfig()
    optimization: OptimizationConfig = OptimizationConfig()
    cache: CacheConfig = CacheConfig()
    objectives: dict[str, ObjectiveConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_mode(self) -> "RunConfig":
        if self.quicktest.strategy is not None and self.quicktest.strategy not in self.strategies:
            raise ValueError(
                f"quicktest.strategy {self.quicktest.strategy!r} is not defined in "
                f"'strategies': {sorted(self.strategies)}"
            )
        if self.regime is not None and self.regime.active:
            for regime_name, ref in self.regime.regimes.items():
                if ref.strategy not in self.strategies:
                    raise ValueError(
                        f"regime {regime_name!r} maps to strategy {ref.strategy!r}, "
                        f"which is not defined in 'strategies': {sorted(self.strategies)}"
                    )
            return self
        active = [name for name, sc in self.strategies.items() if sc.active]
        if len(active) != 1:
            raise ValueError(
                "single-strategy mode (regime inactive) needs exactly one strategy "
                f"with active: true, got {active or 'none'}"
            )
        return self

    def quicktest_view(self) -> QuickTestConfig:
        """The quicktest runner's config, with strategy params resolved from
        the shared ``strategies`` map."""
        name = self.quicktest.strategy
        if name is None:
            active = [n for n, sc in self.strategies.items() if sc.active]
            if len(active) != 1:
                raise ValueError(
                    "quicktest.strategy is not set and no single active strategy exists; "
                    f"set quicktest.strategy to one of {sorted(self.strategies)}"
                )
            name = active[0]
        return QuickTestConfig(
            symbols=self.symbols,
            start=self.data.start,
            end=self.data.end,
            strategy=QuickTestStrategyConfig(name=name, params=self.strategies[name].params),
            capital=self.quicktest.capital,
            position_size_pct=self.quicktest.position_size_pct,
            spread_pct=self.quicktest.spread_pct,
            slippage_pct=self.quicktest.slippage_pct,
            objective=self.quicktest.objective,
            permutation=self.quicktest.permutation,
        )

    def optimize_view(self) -> OptimizeConfig:
        """The optimizer's config, with the window and symbols taken from
        the shared ``data`` / ``symbols`` sections."""
        if self.optimization.study is None:
            raise ValueError("optimization.study is not configured (name, n_trials, ...)")
        if not self.optimization.search_space:
            raise ValueError(
                "optimization.search_space is empty — extend a search-space layer "
                "(config/optimization/*.yaml) or define one in the profile"
            )
        return OptimizeConfig(
            study=self.optimization.study,
            backtest=BacktestWindowConfig(
                start=self.data.start, end=self.data.end, symbols=self.symbols
            ),
            objective=self.optimization.objective,
            constraints=self.optimization.constraints,
            search_space=self.optimization.search_space,
        )


#: placeholder window for legacy stored configs that predate the `data` section
_LEGACY_WINDOW = {"start": "1970-01-01", "end": "1970-01-01"}


def run_config_from_stored(raw: dict[str, Any]) -> RunConfig:
    """Validate a config read back from ``runs.config``.

    Runs persisted before the layered config system stored the old
    ``AppConfig`` shape (``watchlist.symbols``, no ``data`` section); those
    are translated so `carcharoth analyze` keeps working on old runs.
    """
    data = dict(raw)
    if "watchlist" in data:  # legacy AppConfig shape
        data["symbols"] = data.pop("watchlist")["symbols"]
        data.setdefault("data", dict(_LEGACY_WINDOW))
    return RunConfig.model_validate(data)
