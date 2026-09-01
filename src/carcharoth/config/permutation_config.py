from typing import Any

from pydantic import Field, field_validator

from carcharoth.config.strict import StrictModel
from carcharoth.permutation.registry import PERMUTATION_METHODS


class PermutationConfig(StrictModel):
    method: str = "in_sample_bars"
    n_permutations: int = Field(default=100, ge=1)
    seed: int = 42
    significance: float = Field(default=0.05, gt=0, lt=1)
    workers: int = Field(default=0, ge=0)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("method")
    @classmethod
    def _known_method(cls, name: str) -> str:
        if name not in PERMUTATION_METHODS:
            available = ", ".join(sorted(PERMUTATION_METHODS))
            raise ValueError(f"unknown permutation method {name!r}; available: {available}")
        return name
