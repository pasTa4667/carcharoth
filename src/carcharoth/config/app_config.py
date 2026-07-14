"""Application config (watchlist, strategy and risk parameters) from YAML."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from carcharoth.regime.models import Regime


class WatchlistConfig(BaseModel):
    symbols: list[str] = Field(min_length=1)


class EngineConfig(BaseModel):
    tick_interval_seconds: int = Field(default=60, gt=0)


class StrategyConfig(BaseModel):
    #: only consulted in single-strategy mode (regime inactive): the one
    #: active strategy trades every symbol. Ignored under regime-driven mode.
    active: bool = False
    params: dict[str, Any] = Field(default_factory=dict)


class RegimeFeatureConfig(BaseModel):
    weight: float = Field(default=1.0, gt=0)
    params: dict[str, Any] = Field(default_factory=dict)


class RegimeStrategyConfig(BaseModel):
    #: names a key in the top-level `strategies` block; params come from there
    strategy: str


class RegimeConfig(BaseModel):
    #: master switch: true -> the detector picks a strategy per symbol/regime;
    #: false -> the single active strategy in `strategies` trades everything
    active: bool = False
    lookback: int = Field(default=400, gt=1)
    evaluate_every_ticks: int = Field(default=5, gt=0)
    winsorize_sigma: float = Field(default=5.0, gt=0)
    #: when None, warm-up ticks skip trading (same as an unmapped regime)
    default_regime: str | None = None
    features: dict[str, RegimeFeatureConfig] = Field(min_length=1)
    regimes: dict[str, RegimeStrategyConfig]

    @model_validator(mode="after")
    def _validate_regime_names(self) -> "RegimeConfig":
        valid = {regime.value for regime in Regime}
        unknown = set(self.regimes) - valid
        if unknown:
            raise ValueError(f"unknown regimes {sorted(unknown)}; valid: {sorted(valid)}")
        if self.default_regime is not None and self.default_regime not in valid:
            raise ValueError(
                f"unknown default_regime {self.default_regime!r}; valid: {sorted(valid)}"
            )
        return self


class RiskConfig(BaseModel):
    max_position_notional: float = Field(default=1000.0, gt=0)
    max_position_pct_equity: float = Field(default=0.10, gt=0, le=1)
    max_total_exposure_pct: float = Field(default=0.50, gt=0, le=1)
    max_open_positions: int = Field(default=5, gt=0)
    buying_power_buffer: float = Field(default=0.95, gt=0, le=1)
    slippage_buffer: float = Field(default=0.02, ge=0)
    max_daily_loss_pct: float = Field(default=0.03, gt=0, le=1)


class BacktestConfig(BaseModel):
    initial_capital: float = Field(default=100_000.0, gt=0)
    #: synthetic quote spread: bid/ask = close * (1 -/+ spread_pct / 2)
    spread_pct: float = Field(default=0.0005, ge=0)
    #: fills execute this fraction worse than the quoted side
    slippage_pct: float = Field(default=0.0005, ge=0)


class ObjectiveConfig(BaseModel):
    """A named fitness definition: weighted composite of analyzer metrics.

    The weight's sign encodes direction: positive -> higher is better,
    negative -> lower is better. Every run's analysis computes one fitness
    score per named objective, so runs are comparable regardless of what
    launched them (manual, optimizer, ...).
    """

    weights: dict[str, float] = Field(min_length=1)
    #: what to do when a weighted metric is absent from the run's results
    on_missing_metric: Literal["penalize", "zero", "fail"] = "penalize"
    penalty_score: float = -1_000_000.0


class AppConfig(BaseModel):
    watchlist: WatchlistConfig
    engine: EngineConfig = EngineConfig()
    #: strategies keyed by strategy name; each carries its params once
    strategies: dict[str, StrategyConfig] = Field(min_length=1)
    regime: RegimeConfig | None = None
    risk: RiskConfig = RiskConfig()
    backtest: BacktestConfig = BacktestConfig()
    objectives: dict[str, ObjectiveConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_mode(self) -> "AppConfig":
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


def load_config(path: Path) -> AppConfig:
    with path.open() as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw)
