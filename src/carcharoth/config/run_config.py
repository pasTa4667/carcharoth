"""One strict layered config for training, inference, and research flows."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import Field, model_validator

from carcharoth.config.app_config import (
    BacktestConfig,
    CacheConfig,
    EngineConfig,
    ModelConfig,
    ObjectiveConfig,
    RiskConfig,
    TrainingConfig,
)
from carcharoth.config.optimize_config import (
    BacktestWindowConfig,
    ConstraintConfig,
    OptimizeConfig,
    SearchParam,
    StudyConfig,
)
from carcharoth.config.permutation_config import PermutationConfig
from carcharoth.config.quicktest_config import QuickTestConfig
from carcharoth.config.strict import StrictModel
from carcharoth.config.universe_config import UniverseMetadata, UniverseSelection, members_digest


class DataConfig(StrictModel):
    start: date
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
    capital: float = Field(default=10_000.0, gt=0)
    position_size_pct: float = Field(default=0.10, gt=0, le=1)
    spread_pct: float = Field(default=0.0, ge=0)
    slippage_pct: float = Field(default=0.0, ge=0)
    objective: str = "default"
    permutation: PermutationConfig | None = None


class OptimizationConfig(StrictModel):
    study: StudyConfig | None = None
    objective: str = "default"
    constraints: list[ConstraintConfig] = Field(default_factory=list)
    search_space: dict[str, SearchParam] = Field(default_factory=dict)


class RunConfig(StrictModel):
    symbols: list[str] = Field(min_length=1)
    universe: UniverseSelection = UniverseSelection()
    universe_metadata: UniverseMetadata | None = None
    data: DataConfig
    engine: EngineConfig = EngineConfig()
    model: ModelConfig
    training: TrainingConfig
    risk: RiskConfig = RiskConfig()
    backtest: BacktestConfig = BacktestConfig()
    quicktest: QuickTestSectionConfig = QuickTestSectionConfig()
    optimization: OptimizationConfig = OptimizationConfig()
    cache: CacheConfig = CacheConfig()
    objectives: dict[str, ObjectiveConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalized_symbols(self) -> "RunConfig":
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be unique")
        if self.universe.bucket is not None and self.universe_metadata is None:
            raise ValueError("selected universe bucket requires embedded metadata")
        if self.universe_metadata is not None:
            if self.universe.bucket != self.universe_metadata.bucket:
                raise ValueError("universe metadata bucket does not match selection")
            if members_digest(self.symbols) != self.universe_metadata.members_digest:
                raise ValueError("universe metadata does not match symbols")
            if self.universe_metadata.discovery_end >= self.training.train_start:
                raise ValueError("universe discovery must end before training starts")
        return self

    def quicktest_view(self) -> QuickTestConfig:
        return QuickTestConfig(
            symbols=self.symbols,
            start=self.data.start,
            end=self.data.end,
            model=self.model,
            universe_metadata=self.universe_metadata,
            capital=self.quicktest.capital,
            position_size_pct=self.quicktest.position_size_pct,
            spread_pct=self.quicktest.spread_pct,
            slippage_pct=self.quicktest.slippage_pct,
            objective=self.quicktest.objective,
            permutation=self.quicktest.permutation,
        )

    def optimize_view(self) -> OptimizeConfig:
        if self.model.artifact_path is None:
            raise ValueError("model.artifact_path must be explicitly selected for optimization")
        if self.optimization.study is None:
            raise ValueError("optimization.study is not configured")
        if not self.optimization.search_space:
            raise ValueError("optimization.search_space is empty")
        forbidden_prefixes = (
            "model.artifact_path",
            "model.features",
            "model.target",
            "model.timeframe_minutes",
            "model.benchmark_symbol",
            "model.xgboost_params",
            "training.",
            "symbols",
            "universe",
            "universe_metadata",
        )
        forbidden = [
            path for path in self.optimization.search_space if path.startswith(forbidden_prefixes)
        ]
        if forbidden:
            raise ValueError(
                "fixed-artifact optimization cannot change retraining-required paths: "
                f"{sorted(forbidden)}"
            )
        return OptimizeConfig(
            study=self.optimization.study,
            backtest=BacktestWindowConfig(
                start=self.data.start,
                end=self.data.end,
                symbols=self.symbols,
            ),
            objective=self.optimization.objective,
            constraints=self.optimization.constraints,
            search_space=self.optimization.search_space,
        )


def run_config_from_stored(raw: dict[str, Any]) -> RunConfig:
    """Only current model-pipeline configs are replayable."""
    return RunConfig.model_validate(raw)
