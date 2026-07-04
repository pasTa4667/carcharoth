"""Application config (watchlist, strategy and risk parameters) from YAML."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class WatchlistConfig(BaseModel):
    symbols: list[str] = Field(min_length=1)


class EngineConfig(BaseModel):
    tick_interval_seconds: int = Field(default=60, gt=0)
    bar_timeframe_minutes: int = Field(default=5, gt=0)


class StrategyConfig(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class RiskConfig(BaseModel):
    max_position_notional: float = Field(default=1000.0, gt=0)
    max_position_pct_equity: float = Field(default=0.10, gt=0, le=1)
    max_total_exposure_pct: float = Field(default=0.50, gt=0, le=1)
    max_open_positions: int = Field(default=5, gt=0)
    buying_power_buffer: float = Field(default=0.95, gt=0, le=1)
    slippage_buffer: float = Field(default=0.02, ge=0)
    max_daily_loss_pct: float = Field(default=0.03, gt=0, le=1)


class AppConfig(BaseModel):
    watchlist: WatchlistConfig
    engine: EngineConfig = EngineConfig()
    strategy: StrategyConfig
    risk: RiskConfig = RiskConfig()


def load_config(path: Path) -> AppConfig:
    with path.open() as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw)
