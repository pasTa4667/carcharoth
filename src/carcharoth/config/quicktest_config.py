"""Validated fixed-artifact quicktest configuration."""

from datetime import UTC, date, datetime, timedelta

from pydantic import Field, model_validator

from carcharoth.config.app_config import ModelConfig
from carcharoth.config.permutation_config import PermutationConfig
from carcharoth.config.strict import StrictModel


class QuickTestConfig(StrictModel):
    symbols: list[str] = Field(min_length=1)
    start: date
    end: date
    model: ModelConfig
    capital: float = Field(default=10_000.0, gt=0)
    position_size_pct: float = Field(default=0.10, gt=0, le=1)
    spread_pct: float = Field(default=0.0, ge=0)
    slippage_pct: float = Field(default=0.0, ge=0)
    objective: str = "default"
    permutation: PermutationConfig | None = None

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
