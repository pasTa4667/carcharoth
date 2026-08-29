"""Shared policy for choosing between long and short entry opportunities."""

from pydantic import Field

from carcharoth.config.strict import StrictModel


class DirectionalConfig(StrictModel):
    shorting_enabled: bool = False
    min_entry_score: float = Field(default=0.5, ge=0, le=1)
    min_score_margin: float = Field(default=0.1, ge=0, le=1)
