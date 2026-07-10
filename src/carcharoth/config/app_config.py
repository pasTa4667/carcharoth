"""Application config (watchlist, strategy and risk parameters) from YAML."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from carcharoth.regime.models import Regime


class WatchlistConfig(BaseModel):
    symbols: list[str] = Field(min_length=1)


class EngineConfig(BaseModel):
    tick_interval_seconds: int = Field(default=60, gt=0)


class StrategyConfig(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class RegimeFeatureConfig(BaseModel):
    weight: float = Field(default=1.0, gt=0)
    params: dict[str, Any] = Field(default_factory=dict)


class RegimeStrategyConfig(BaseModel):
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)


class RegimeConfig(BaseModel):
    lookback: int = Field(default=400, gt=1)
    evaluate_every_ticks: int = Field(default=5, gt=0)
    winsorize_sigma: float = Field(default=5.0, gt=0)
    default_regime: str = Regime.MEAN_REVERTING.value
    features: dict[str, RegimeFeatureConfig] = Field(min_length=1)
    regimes: dict[str, RegimeStrategyConfig]

    @model_validator(mode="after")
    def _validate_regime_names(self) -> "RegimeConfig":
        valid = {regime.value for regime in Regime}
        unknown = set(self.regimes) - valid
        if unknown:
            raise ValueError(f"unknown regimes {sorted(unknown)}; valid: {sorted(valid)}")
        missing = valid - set(self.regimes)
        if missing:
            raise ValueError(f"regimes without a mapped strategy: {sorted(missing)}")
        if self.default_regime not in valid:
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


class AppConfig(BaseModel):
    watchlist: WatchlistConfig
    engine: EngineConfig = EngineConfig()
    strategy: StrategyConfig | None = None
    regime: RegimeConfig | None = None
    risk: RiskConfig = RiskConfig()
    backtest: BacktestConfig = BacktestConfig()

    @model_validator(mode="after")
    def _exactly_one_strategy_source(self) -> "AppConfig":
        if (self.strategy is None) == (self.regime is None):
            raise ValueError(
                "exactly one of 'strategy' (single-strategy mode) or 'regime' "
                "(regime-driven mode) must be configured"
            )
        return self


def load_config(path: Path) -> AppConfig:
    with path.open() as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw)
