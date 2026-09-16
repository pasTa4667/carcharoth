import json
from datetime import UTC, date, datetime

import pytest

from carcharoth.config.loader import load_profile
from carcharoth.config.universe_config import UniverseMetadata
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.modeling.artifact import (
    MANIFEST_FILENAME,
    MODEL_FILENAME,
    ArtifactError,
    ArtifactManifest,
    file_sha256,
    validate_artifact,
)


def test_artifact_hash_schema_and_out_of_sample_guard(tmp_path) -> None:
    config = load_profile("training").config.model
    pipeline = FeaturePipeline(config.features, config.timeframe_minutes, config.benchmark_symbol)
    model_path = tmp_path / MODEL_FILENAME
    model_path.write_bytes(b"model")
    manifest = ArtifactManifest(
        model_id="m1",
        model_sha256=file_sha256(model_path),
        config_hash="cfg",
        schema_hash=pipeline.schema_hash,
        feature_names=list(pipeline.feature_names),
        feature_descriptors=[item.payload() for item in pipeline.descriptors],
        task=config.target.type,
        target=config.target.model_dump(mode="json"),
        timeframe_minutes=config.timeframe_minutes,
        benchmark_symbol=config.benchmark_symbol,
        symbols=["AAPL"],
        xgboost_params=dict(config.xgboost_params),
        seed=42,
        train_start=date(2024, 1, 1),
        train_end=date(2024, 6, 30),
        validation_start=date(2024, 7, 1),
        validation_end=date(2024, 12, 31),
        train_rows=10,
        validation_rows=5,
        created_at=datetime.now(UTC),
    )
    (tmp_path / MANIFEST_FILENAME).write_text(
        json.dumps(manifest.model_dump(mode="json"), default=str)
    )
    loaded, loaded_path = validate_artifact(
        tmp_path, pipeline, config, evaluation_start=date(2025, 1, 1)
    )
    assert loaded.model_id == "m1"
    assert loaded_path == model_path
    with pytest.raises(ArtifactError, match="out-of-sample"):
        validate_artifact(tmp_path, pipeline, config, evaluation_start=date(2024, 12, 31))
    future_universe = UniverseMetadata(
        snapshot_digest="snapshot",
        bucket="all",
        members_digest="members",
        discovery_start=date(2023, 1, 1),
        discovery_end=date(2024, 1, 1),
        feed="sip",
    )
    with pytest.raises(ArtifactError, match="evaluation universe discovery"):
        validate_artifact(tmp_path, pipeline, config, evaluation_universe=future_universe)

    model_path.write_bytes(b"corrupt")
    with pytest.raises(ArtifactError, match="hash"):
        validate_artifact(tmp_path, pipeline, config)
