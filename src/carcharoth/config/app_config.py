"""Config sections shared by training, model runtime, replay, and risk.

The root model that composes these sections is ``RunConfig`` in
``carcharoth.config.run_config``; files are loaded and layered by
``carcharoth.config.loader``.
"""

from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from carcharoth.config.permutation_config import PermutationConfig
from carcharoth.config.strict import StrictModel


class EngineConfig(StrictModel):
    tick_interval_seconds: int = Field(default=60, gt=0)


class RegimeFeatureConfig(StrictModel):
    weight: float = Field(default=1.0, gt=0)
    params: dict[str, Any] = Field(default_factory=dict)


class ScoreDetectorConfig(StrictModel):
    """The evidence-based score detector (trending <-> mean_reverting)."""

    evaluate_interval_minutes: int = Field(default=5, gt=0)
    lookback: int = Field(default=400, gt=1)
    winsorize_sigma: float = Field(default=5.0, gt=0)
    features: dict[str, RegimeFeatureConfig] = Field(min_length=1)


class HmmDetectorConfig(StrictModel):
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


class RegimeConfig(StrictModel):
    """Standalone detector config retained for possible future model context."""

    detector: Literal["score", "hmm"] = "score"
    score: ScoreDetectorConfig | None = None
    hmm: HmmDetectorConfig | None = None

    @model_validator(mode="after")
    def _selected_detector_exists(self) -> "RegimeConfig":
        selected = self.score if self.detector == "score" else self.hmm
        if selected is None:
            raise ValueError(
                f"regime.detector is {self.detector!r} but the '{self.detector}' section is missing"
            )
        return self

    @property
    def evaluate_interval_minutes(self) -> int:
        """Re-assessment cadence of the selected detector."""
        selected = self.score if self.detector == "score" else self.hmm
        assert selected is not None  # enforced by the validator
        return selected.evaluate_interval_minutes


class FeatureSpecConfig(StrictModel):
    """One ordered feature-registry entry.

    A feature implementation may use params to derive its output name (for
    example log_return(period=5) -> return_5). Lists are replaced wholesale by
    the layered config loader, making feature membership explicit.
    """

    name: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class RegressionTargetConfig(StrictModel):
    type: Literal["regression"] = "regression"
    horizon_bars: int = Field(default=5, gt=0)


class ClassificationTargetConfig(StrictModel):
    type: Literal["classification"] = "classification"
    horizon_bars: int = Field(default=5, gt=0)
    neutral_band: float = Field(default=0.0005, ge=0)


TargetConfig = Annotated[
    RegressionTargetConfig | ClassificationTargetConfig,
    Field(discriminator="type"),
]


class DecisionPolicyConfig(StrictModel):
    #: Regression values are log-return fractions (0.001 = 10 bps).
    long_entry_threshold: float = Field(default=0.0005, ge=0)
    short_entry_threshold: float = Field(default=-0.0005, le=0)
    long_exit_threshold: float = Field(default=0.0)
    short_exit_threshold: float = Field(default=0.0)
    #: Classification entry confidence; argmax FLAT below this threshold.
    class_probability_threshold: float = Field(default=0.55, gt=0, le=1)
    entry_delay_minutes: int = Field(default=15, ge=0)
    entry_cutoff_minutes: int = Field(default=15, ge=0)
    flatten_minutes: int = Field(default=10, gt=0)

    @model_validator(mode="after")
    def _threshold_order(self) -> "DecisionPolicyConfig":
        if self.long_exit_threshold > self.long_entry_threshold:
            raise ValueError("long_exit_threshold must be <= long_entry_threshold")
        if self.short_exit_threshold < self.short_entry_threshold:
            raise ValueError("short_exit_threshold must be >= short_entry_threshold")
        if self.flatten_minutes < 5:
            raise ValueError("flatten_minutes must leave at least one five-minute fill bar")
        return self


class ModelConfig(StrictModel):
    prepared_history: bool = False
    artifact_path: Path | None = None
    timeframe_minutes: int = Field(default=5, gt=0)
    benchmark_symbol: str = Field(default="SPY", min_length=1)
    features: list[FeatureSpecConfig] = Field(min_length=1)
    target: TargetConfig = RegressionTargetConfig()
    decision: DecisionPolicyConfig = DecisionPolicyConfig()
    #: Native xgboost params; objective/eval_metric/seed are owned by the adapter.
    xgboost_params: dict[str, bool | int | float | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _session_timing(self) -> "ModelConfig":
        if self.decision.flatten_minutes < self.timeframe_minutes:
            raise ValueError("model.decision.flatten_minutes must be >= timeframe_minutes")
        if self.decision.entry_cutoff_minutes < self.decision.flatten_minutes:
            raise ValueError("entry_cutoff_minutes must be >= flatten_minutes")
        return self


class EligibilityConfig(StrictModel):
    """Operational data floors, not a guarantee of statistical adequacy."""

    mode: Literal["drop", "strict"] = "drop"
    min_train_rows: int = Field(default=100, gt=0)
    min_validation_rows: int = Field(default=30, gt=0)
    min_usable_coverage: float = Field(default=0.80, gt=0, le=1)
    min_symbols: int = Field(default=1, gt=0)
    min_retained_fraction: float = Field(default=0.50, gt=0, le=1)


class TrainingConfig(StrictModel):
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    artifact_root: Path = Path("artifacts/models")
    seed: int = 42
    early_stopping_rounds: int = Field(default=50, gt=0)
    eligibility: EligibilityConfig = EligibilityConfig()

    @model_validator(mode="after")
    def _chronological_windows(self) -> "TrainingConfig":
        if self.train_end < self.train_start:
            raise ValueError("training.train_end must not be before train_start")
        if self.validation_end < self.validation_start:
            raise ValueError("training.validation_end must not be before validation_start")
        if self.validation_start <= self.train_end:
            raise ValueError("validation_start must be after train_end")
        return self


class RiskConfig(StrictModel):
    max_position_notional: float = Field(default=1000.0, gt=0)
    max_position_pct_equity: float = Field(default=0.10, gt=0, le=1)
    max_total_exposure_pct: float = Field(default=0.50, gt=0, le=1)
    max_open_positions: int = Field(default=5, gt=0)
    buying_power_buffer: float = Field(default=0.95, gt=0, le=1)
    slippage_buffer: float = Field(default=0.02, ge=0)
    max_daily_loss_pct: float = Field(default=0.03, gt=0, le=1)


class BacktestConfig(StrictModel):
    check_data_coverage: bool = False
    initial_capital: float = Field(default=100_000.0, gt=0)
    #: Gross buying-power multiple used by the signed-position simulator.
    margin_multiplier: float = Field(default=2.0, ge=1)
    #: synthetic quote spread: bid/ask = close * (1 -/+ spread_pct / 2)
    spread_pct: float = Field(default=0.0005, ge=0)
    #: fills execute this fraction worse than the quoted side
    slippage_pct: float = Field(default=0.0005, ge=0)
    #: monte carlo trade-shuffle settings for `backtest --permute`; only
    #: trade-based methods work here (bar methods need engine re-runs)
    permutation: PermutationConfig | None = None


class CacheConfig(StrictModel):
    """Persistent Redis cache for backtest/optimize runs (the live PAPER
    path never uses it). Runs degrade to no caching, with a warning, when
    Redis is unreachable."""

    #: master switch; false behaves exactly as before the cache existed
    enabled: bool = True
    #: historical Alpaca bars, gap-filled per (timeframe, symbol)
    bars: bool = True


class ObjectiveConfig(StrictModel):
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
