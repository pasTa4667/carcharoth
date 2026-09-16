"""Strict configuration and manifest models for offline universe discovery."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from carcharoth.config.strict import StrictModel

DESCRIPTOR_NAMES = (
    "log_dollar_volume",
    "log_realized_volatility",
    "log_intraday_range",
    "log_overnight_gap",
    "market_beta",
    "return_autocorrelation",
)


class UniverseSelection(StrictModel):
    snapshot: Path | None = None
    bucket: str | None = None

    @model_validator(mode="after")
    def _snapshot_required(self) -> UniverseSelection:
        if self.bucket is not None and self.snapshot is None:
            raise ValueError("universe.snapshot is required when universe.bucket is set")
        return self


class UniverseMetadata(StrictModel):
    snapshot_digest: str
    bucket: str
    members_digest: str
    discovery_start: date
    discovery_end: date
    feed: Literal["sip", "iex"]
    survivorship_biased: bool = True


class UniverseDiscoveryConfig(StrictModel):
    start: date
    end: date
    top_n: int = Field(default=500, gt=1)
    feed: Literal["sip", "iex"] = "sip"
    benchmark_symbol: str = "SPY"
    min_observations: int = Field(default=252, gt=2)
    min_coverage: float = Field(default=0.95, gt=0, le=1)
    exclude_symbols: list[str] = Field(default_factory=list)
    distance_threshold: float = Field(default=1.0, gt=0)
    min_bucket_size: int = Field(default=10, gt=1)
    feature_weights: dict[str, float] = Field(
        default_factory=lambda: dict.fromkeys(DESCRIPTOR_NAMES, 1.0)
    )
    output_root: Path = Path("artifacts/universes")

    @model_validator(mode="after")
    def _valid(self) -> UniverseDiscoveryConfig:
        if self.end < self.start:
            raise ValueError("end must not be before start")
        if self.start < date(2016, 1, 1):
            raise ValueError("Alpaca stock history starts in 2016")
        if self.end >= date.today():
            raise ValueError("discovery end must be before today")
        unknown = set(self.feature_weights) - set(DESCRIPTOR_NAMES)
        if unknown:
            raise ValueError(f"unknown feature weights: {sorted(unknown)}")
        if any(not 0 <= weight < float("inf") for weight in self.feature_weights.values()):
            raise ValueError("feature weights must be finite and nonnegative")
        if not any(self.feature_weights.values()):
            raise ValueError("at least one feature weight must be positive")
        if len(set(self.exclude_symbols)) != len(self.exclude_symbols):
            raise ValueError("exclude_symbols must be unique")
        return self


class UniverseSnapshot(StrictModel):
    format_version: Literal[1] = 1
    content_digest: str = ""
    created_at: datetime
    discovery: UniverseDiscoveryConfig
    asset_catalogue_retrieved_at: datetime
    asset_catalogue: Literal["current", "reused"]
    survivorship_biased: bool = True
    adjustment: Literal["split"] = "split"
    asof: str = "-"
    buckets: dict[str, list[str]]
    undersized_clusters: list[list[str]] = Field(default_factory=list)
    scaling: dict[str, dict[str, float]]
    input_checksums: dict[str, str]

    @model_validator(mode="after")
    def _membership(self) -> UniverseSnapshot:
        if "all" not in self.buckets:
            raise ValueError("snapshot must contain the reserved 'all' bucket")
        for name, symbols in self.buckets.items():
            if not symbols or len(symbols) != len(set(symbols)) or symbols != sorted(symbols):
                raise ValueError(f"bucket {name!r} must contain sorted unique symbols")
        all_symbols = set(self.buckets["all"])
        seen: set[str] = set()
        clusters = [
            *self.buckets.items(),
            *(("undersized", value) for value in self.undersized_clusters),
        ]
        for name, symbols in clusters:
            if name == "all":
                continue
            members = set(symbols)
            if not members or len(members) != len(symbols) or list(symbols) != sorted(symbols):
                raise ValueError(f"cluster {name!r} must contain sorted unique symbols")
            if not members <= all_symbols:
                raise ValueError(f"cluster {name!r} contains symbols outside 'all'")
            if seen & members:
                raise ValueError("snapshot clusters must be disjoint")
            seen |= members
        if seen != all_symbols:
            raise ValueError("every 'all' symbol must belong to one cluster")
        return self


def members_digest(symbols: list[str]) -> str:
    return hashlib.sha256("\n".join(symbols).encode()).hexdigest()


def snapshot_digest(snapshot: UniverseSnapshot) -> str:
    payload = snapshot.model_dump(mode="json")
    payload["content_digest"] = ""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_universe_snapshot(path: Path) -> UniverseSnapshot:
    manifest = path / "buckets.yaml" if path.is_dir() else path
    try:
        raw: Any = yaml.safe_load(manifest.read_text())
        snapshot = UniverseSnapshot.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"invalid universe snapshot {manifest}: {exc}") from exc
    if snapshot_digest(snapshot) != snapshot.content_digest:
        raise ValueError(f"universe snapshot digest mismatch: {manifest}")
    return snapshot
