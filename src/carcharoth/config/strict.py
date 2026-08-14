"""Shared strict base model for every config section.

``extra="forbid"`` everywhere: with layered configs a typo'd key must fail
loudly instead of being silently ignored. Layer files themselves are
additionally checked against the base layer's structure by the loader
(see ``carcharoth.config.loader``); this model-level strictness is the last
line of defense for the base layer and for configs read back from the DB.
"""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
