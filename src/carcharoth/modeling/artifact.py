"""Immutable XGBoost artifact manifest and compatibility checks."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from carcharoth.config.app_config import ModelConfig
from carcharoth.config.strict import StrictModel
from carcharoth.config.universe_config import UniverseMetadata
from carcharoth.domain.models import ModelTask
from carcharoth.features.pipeline import FeaturePipeline

ARTIFACT_FORMAT_VERSION = 1
MODEL_FILENAME = "model.ubj"
MANIFEST_FILENAME = "manifest.json"
REPORT_FILENAME = "report.json"


class ArtifactManifest(StrictModel):
    format_version: int = ARTIFACT_FORMAT_VERSION
    model_id: str
    model_sha256: str
    config_hash: str
    schema_hash: str
    feature_names: list[str]
    feature_descriptors: list[dict[str, Any]]
    task: ModelTask
    target: dict[str, Any]
    timeframe_minutes: int
    benchmark_symbol: str
    symbols: list[str]
    effective_symbols: list[str] | None = None
    eligibility_assessment_id: str | None = None
    universe_metadata: UniverseMetadata | None = None
    xgboost_params: dict[str, bool | int | float | str]
    seed: int
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    train_rows: int
    validation_rows: int
    metrics: dict[str, float] = Field(default_factory=dict)
    versions: dict[str, str] = Field(default_factory=dict)
    created_at: datetime

    @model_validator(mode="after")
    def _effective_membership(self) -> ArtifactManifest:
        if self.effective_symbols is not None:
            members = self.effective_symbols
            if (
                not members
                or len(members) != len(set(members))
                or not set(members) <= set(self.symbols)
            ):
                raise ValueError("effective_symbols must be a nonempty unique subset of symbols")
        return self


class ArtifactError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> ArtifactManifest:
    try:
        raw = json.loads(path.read_text())
        return ArtifactManifest.model_validate(raw)
    except Exception as exc:
        raise ArtifactError(f"invalid model manifest {path}: {exc}") from exc


def validate_artifact(
    artifact_dir: Path,
    pipeline: FeaturePipeline,
    config: ModelConfig,
    evaluation_start: date | None = None,
    evaluation_universe: UniverseMetadata | None = None,
) -> tuple[ArtifactManifest, Path]:
    manifest_path = artifact_dir / MANIFEST_FILENAME
    model_path = artifact_dir / MODEL_FILENAME
    if not manifest_path.is_file() or not model_path.is_file():
        raise ArtifactError(
            f"artifact {artifact_dir} must contain {MANIFEST_FILENAME} and {MODEL_FILENAME}"
        )
    manifest = load_manifest(manifest_path)
    if manifest.format_version != ARTIFACT_FORMAT_VERSION:
        raise ArtifactError(
            f"artifact format {manifest.format_version} is unsupported; "
            f"expected {ARTIFACT_FORMAT_VERSION}"
        )
    actual_hash = file_sha256(model_path)
    if actual_hash != manifest.model_sha256:
        raise ArtifactError("model hash does not match manifest")
    if manifest.schema_hash != pipeline.schema_hash:
        raise ArtifactError(
            f"feature schema mismatch: artifact={manifest.schema_hash} "
            f"runtime={pipeline.schema_hash}; HMM-enabled and --no-hmm runs require "
            "separately trained matching artifacts"
        )
    if manifest.feature_names != list(pipeline.feature_names):
        raise ArtifactError("artifact feature order does not match runtime pipeline")
    if manifest.task.value != config.target.type:
        raise ArtifactError(
            f"target task mismatch: artifact={manifest.task.value} config={config.target.type}"
        )
    if manifest.target != config.target.model_dump(mode="json"):
        raise ArtifactError("target configuration does not match artifact")
    if manifest.timeframe_minutes != config.timeframe_minutes:
        raise ArtifactError("model timeframe does not match artifact")
    if manifest.benchmark_symbol != config.benchmark_symbol:
        raise ArtifactError("benchmark symbol does not match artifact")
    if manifest.xgboost_params != config.xgboost_params:
        raise ArtifactError("XGBoost parameter configuration does not match artifact")
    if evaluation_start is not None and evaluation_start <= manifest.validation_end:
        raise ArtifactError(
            f"evaluation starts {evaluation_start} but artifact validation ends "
            f"{manifest.validation_end}; use a strictly out-of-sample window"
        )
    for label, universe in (
        ("training", manifest.universe_metadata),
        ("evaluation", evaluation_universe),
    ):
        if universe is not None and universe.discovery_end >= manifest.train_start:
            raise ArtifactError(
                f"{label} universe discovery ends {universe.discovery_end} but artifact training "
                f"starts {manifest.train_start}"
            )
    return manifest, model_path
