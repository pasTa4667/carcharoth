"""Optimization study config (search space, objective reference).

Which parameters are searched is fully config-driven: each search-space key
is a dot-path into the resolved run config (e.g.
``strategies.mean_reversion.params.entry_z``), so changing what gets
optimized never requires a code change.

This model is no longer loaded from its own YAML file; it is derived from
the resolved layered config via ``RunConfig.optimize_view()`` (the study
settings and search space live in the ``optimization:`` section, symbols
and the date window in the shared ``symbols`` / ``data`` sections).
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, model_validator

from carcharoth.config.strict import StrictModel


class StudyConfig(StrictModel):
    #: studies are resumable: re-running with the same name continues it
    name: str = Field(min_length=1)
    n_trials: int = Field(gt=0)
    #: parallel worker processes; the trial budget is split across them
    workers: int = Field(default=1, ge=1)
    #: seed for the sampler; omit for nondeterministic sampling.
    #: With workers > 1 each worker derives its own seed (seed + index),
    #: so results are no longer reproducible run-to-run.
    sampler_seed: int | None = None


class BacktestWindowConfig(StrictModel):
    start: date
    #: inclusive, like the backtest CLI's --end
    end: date
    #: defaults to the base config's watchlist
    symbols: list[str] | None = None

    @model_validator(mode="after")
    def _start_before_end(self) -> "BacktestWindowConfig":
        if self.end < self.start:
            raise ValueError("backtest end must not be before start")
        return self

    @property
    def start_dt(self) -> datetime:
        return datetime(self.start.year, self.start.month, self.start.day, tzinfo=UTC)

    @property
    def end_exclusive_dt(self) -> datetime:
        return datetime(self.end.year, self.end.month, self.end.day, tzinfo=UTC) + timedelta(days=1)


class ConstraintConfig(StrictModel):
    """Hard constraint on a portfolio-level metric; violations make the
    trial infeasible for the sampler (the run's stored fitness is untouched)."""

    metric: str
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _at_least_one_bound(self) -> "ConstraintConfig":
        if self.min is None and self.max is None:
            raise ValueError(f"constraint on {self.metric!r} needs min and/or max")
        return self


class IntParam(StrictModel):
    type: Literal["int"]
    low: int
    high: int
    step: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def _low_below_high(self) -> "IntParam":
        if self.low >= self.high:
            raise ValueError("low must be < high")
        return self


class FloatParam(StrictModel):
    type: Literal["float"]
    low: float
    high: float
    step: float | None = Field(default=None, gt=0)
    log: bool = False

    @model_validator(mode="after")
    def _validate_range(self) -> "FloatParam":
        if self.low >= self.high:
            raise ValueError("low must be < high")
        if self.step is not None and self.log:
            raise ValueError("step and log are mutually exclusive")
        return self


class CategoricalParam(StrictModel):
    type: Literal["categorical"]
    choices: list[bool | int | float | str] = Field(min_length=2)


SearchParam = Annotated[IntParam | FloatParam | CategoricalParam, Field(discriminator="type")]


class OptimizeConfig(StrictModel):
    study: StudyConfig
    backtest: BacktestWindowConfig
    #: name of an objective defined in the base config's `objectives:` section
    objective: str = "default"
    constraints: list[ConstraintConfig] = Field(default_factory=list)
    #: dot-path into the resolved run config -> parameter distribution
    search_space: dict[str, SearchParam] = Field(min_length=1)
