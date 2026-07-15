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


class ScoreDetectorConfig(BaseModel):
    """The evidence-based score detector (trending <-> mean_reverting)."""

    evaluate_interval_minutes: int = Field(default=5, gt=0)
    lookback: int = Field(default=400, gt=1)
    winsorize_sigma: float = Field(default=5.0, gt=0)
    features: dict[str, RegimeFeatureConfig] = Field(min_length=1)


class HmmDetectorConfig(BaseModel):
    """The Gaussian-HMM detector (trending_up / trending_down / range_bound /
    high_volatility), emitting posterior probabilities per regime."""

    evaluate_interval_minutes: int = Field(default=30, gt=0)
    #: >= 4 so every regime can be represented; extra states become RANGE_BOUND
    n_states: int = Field(default=4, ge=4)
    #: bars the model is trained on (~20 sessions of 78 five-minute bars)
    training_window: int = Field(default=1560, gt=1)
    #: refit once this many new bars have arrived since the last fit (~1 session)
    refit_interval_bars: int = Field(default=78, gt=0)
    #: below this top-regime probability the previous regime is kept
    min_confidence: float = Field(default=0.5, ge=0, le=1)
    seed: int = 42
    n_restarts: int = Field(default=2, gt=0)
    covariance_type: Literal["diag", "full"] = "diag"
    n_iter: int = Field(default=100, gt=0)
    tol: float = Field(default=1.0e-3, gt=0)
    min_covar: float = Field(default=1.0e-3, gt=0)
    winsorize_sigma: float = Field(default=5.0, gt=0)
    vol_window: int = Field(default=20, gt=1)
    ema_period: int = Field(default=50, gt=1)
    adx_period: int = Field(default=14, gt=1)


class RegimeConfig(BaseModel):
    #: master switch: true -> the detector picks a strategy per symbol/regime;
    #: false -> the single active strategy in `strategies` trades everything
    active: bool = False
    #: which detector implementation classifies regimes
    detector: Literal["score", "hmm"] = "score"
    #: when None, warm-up ticks skip trading (same as an unmapped regime)
    default_regime: str | None = None
    score: ScoreDetectorConfig | None = None
    hmm: HmmDetectorConfig | None = None
    regimes: dict[str, RegimeStrategyConfig]

    @model_validator(mode="after")
    def _validate_regime_names(self) -> "RegimeConfig":
        selected = self.score if self.detector == "score" else self.hmm
        if selected is None:
            raise ValueError(
                f"regime.detector is {self.detector!r} but the '{self.detector}' section is missing"
            )
        valid = {regime.value for regime in Regime}
        unknown = set(self.regimes) - valid
        if unknown:
            raise ValueError(f"unknown regimes {sorted(unknown)}; valid: {sorted(valid)}")
        if self.default_regime is not None and self.default_regime not in valid:
            raise ValueError(
                f"unknown default_regime {self.default_regime!r}; valid: {sorted(valid)}"
            )
        return self

    @property
    def evaluate_interval_minutes(self) -> int:
        """Re-assessment cadence of the selected detector."""
        selected = self.score if self.detector == "score" else self.hmm
        assert selected is not None  # enforced by the validator
        return selected.evaluate_interval_minutes


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


class CacheConfig(BaseModel):
    """Persistent Redis cache for backtest/optimize runs (the live PAPER
    path never uses it). Runs degrade to no caching, with a warning, when
    Redis is unreachable."""

    #: master switch; false behaves exactly as before the cache existed
    enabled: bool = True
    #: historical Alpaca bars, gap-filled per (timeframe, symbol)
    bars: bool = True
    #: fitted HMM models; disable when an Optuna study searches HMM params
    #: (every trial would get a fresh config hash and never hit)
    hmm: bool = True


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
    cache: CacheConfig = CacheConfig()
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
