"""Quick-test config (symbols, window, strategy, simulation settings) from YAML.

The quick-test runner is intentionally minimal: one strategy, a symbol list,
and a time window. Everything else (regime detection, risk management) is
deliberately absent — see `carcharoth.quicktest`.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from carcharoth.strategies.registry import STRATEGIES


class QuickTestStrategyConfig(BaseModel):
    name: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _known_strategy(cls, name: str) -> str:
        if name not in STRATEGIES:
            available = ", ".join(sorted(STRATEGIES))
            raise ValueError(f"unknown strategy {name!r}; available: {available}")
        return name


class QuickTestConfig(BaseModel):
    symbols: list[str] = Field(min_length=1)
    start: date
    #: inclusive, like the backtest CLI's --end
    end: date
    strategy: QuickTestStrategyConfig
    #: starting capital per symbol (each symbol is simulated independently)
    capital: float = Field(default=10_000.0, gt=0)
    #: buy notional as a fraction of `capital`
    position_size_pct: float = Field(default=0.10, gt=0, le=1)
    #: synthetic quote spread: fills at close * (1 +/- spread_pct / 2); 0 = frictionless
    spread_pct: float = Field(default=0.0, ge=0)
    #: fills execute this fraction worse than the quoted side; 0 = frictionless
    slippage_pct: float = Field(default=0.0, ge=0)
    #: named objective from the base config's `objectives:` used for fitness
    objective: str = "default"

    @model_validator(mode="after")
    def _start_before_end(self) -> "QuickTestConfig":
        if self.end < self.start:
            raise ValueError("end must not be before start")
        return self

    @property
    def start_dt(self) -> datetime:
        return datetime(self.start.year, self.start.month, self.start.day, tzinfo=UTC)

    @property
    def end_exclusive_dt(self) -> datetime:
        return datetime(self.end.year, self.end.month, self.end.day, tzinfo=UTC) + timedelta(days=1)


def load_quicktest_config(path: Path) -> QuickTestConfig:
    with path.open() as f:
        raw = yaml.safe_load(f)
    return QuickTestConfig.model_validate(raw)
