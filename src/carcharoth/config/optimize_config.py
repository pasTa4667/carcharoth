"""Optimization study config (search space, objective reference) from YAML.

Which parameters are searched is fully config-driven: each search-space key
is a dot-path into the app config (e.g.
``strategies.mean_reversion.params.entry_z``), so changing what gets
optimized never requires a code change.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class StudyConfig(BaseModel):
    #: studies are resumable: re-running with the same name continues it
    name: str = Field(min_length=1)
    n_trials: int = Field(gt=0)
    #: parallel worker processes; the trial budget is split across them
    workers: int = Field(default=1, ge=1)
    #: seed for the sampler; omit for nondeterministic sampling.
    #: With workers > 1 each worker derives its own seed (seed + index),
    #: so results are no longer reproducible run-to-run.
    sampler_seed: int | None = None


class BacktestWindowConfig(BaseModel):
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


class ConstraintConfig(BaseModel):
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


class IntParam(BaseModel):
    type: Literal["int"]
    low: int
    high: int
    step: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def _low_below_high(self) -> "IntParam":
        if self.low >= self.high:
            raise ValueError("low must be < high")
        return self


class FloatParam(BaseModel):
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


class CategoricalParam(BaseModel):
    type: Literal["categorical"]
    choices: list[bool | int | float | str] = Field(min_length=2)


SearchParam = Annotated[IntParam | FloatParam | CategoricalParam, Field(discriminator="type")]


class OptimizeConfig(BaseModel):
    study: StudyConfig
    backtest: BacktestWindowConfig
    #: name of an objective defined in the base config's `objectives:` section
    objective: str = "default"
    constraints: list[ConstraintConfig] = Field(default_factory=list)
    #: dot-path into the app config -> parameter distribution
    search_space: dict[str, SearchParam] = Field(min_length=1)


def load_optimize_config(path: Path) -> OptimizeConfig:
    with path.open() as f:
        raw = yaml.safe_load(f)
    return OptimizeConfig.model_validate(raw)
