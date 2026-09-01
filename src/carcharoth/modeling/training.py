"""Offline global dataset -> immutable XGBoost artifact orchestration."""

from __future__ import annotations

import json
import os
import platform
import shutil
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

import xgboost

from carcharoth.config.run_config import RunConfig
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.interfaces.optimization import BarsFetcher
from carcharoth.modeling.artifact import (
    MANIFEST_FILENAME,
    MODEL_FILENAME,
    REPORT_FILENAME,
    ArtifactManifest,
    file_sha256,
)
from carcharoth.modeling.dataset import build_dataset
from carcharoth.modeling.diagnostics import dataset_diagnostics
from carcharoth.services.alpaca.historical import warmup_window
from carcharoth.services.xgboost import train_model


def _start(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _end_exclusive(value: date) -> datetime:
    return _start(value) + timedelta(days=1)


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def train_and_publish(
    config: RunConfig,
    config_hash: str,
    fetch_bars: BarsFetcher,
) -> Path:
    model = config.model
    training = config.training
    pipeline = FeaturePipeline(
        model.features,
        model.timeframe_minutes,
        model.benchmark_symbol,
    )
    symbols = list(dict.fromkeys([*config.symbols, model.benchmark_symbol]))
    spec = pipeline.required_bars()
    fetch_start = _start(training.train_start) - warmup_window(spec)
    fetch_end = _end_exclusive(training.validation_end)
    bars = fetch_bars(symbols, spec.timeframe, fetch_start, fetch_end)

    train = build_dataset(
        bars,
        config.symbols,
        pipeline,
        model.target,
        _start(training.train_start),
        _end_exclusive(training.train_end),
    )
    validation = build_dataset(
        bars,
        config.symbols,
        pipeline,
        model.target,
        _start(training.validation_start),
        _end_exclusive(training.validation_end),
    )
    train_diagnostics = dataset_diagnostics(train)
    validation_diagnostics = dataset_diagnostics(validation)
    constants = train_diagnostics["constant_features"]
    if constants:
        raise ValueError(f"constant training features are not useful: {constants}")

    result = train_model(
        train,
        validation,
        model.xgboost_params,
        training.seed,
        training.early_stopping_rounds,
    )

    created_at = datetime.now(UTC)
    model_id = f"{created_at:%Y%m%dT%H%M%S%fZ}-{config_hash[:8]}"
    root = training.artifact_root
    target_dir = root / model_id
    if target_dir.exists():
        raise FileExistsError(f"immutable artifact already exists: {target_dir}")
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".{model_id}.tmp-{uuid4().hex}"
    temporary.mkdir()
    try:
        model_path = temporary / MODEL_FILENAME
        result.save_model(model_path)
        manifest = ArtifactManifest(
            model_id=model_id,
            model_sha256=file_sha256(model_path),
            config_hash=config_hash,
            schema_hash=pipeline.schema_hash,
            feature_names=list(pipeline.feature_names),
            feature_descriptors=[descriptor.payload() for descriptor in pipeline.descriptors],
            task=train.task,
            target=model.target.model_dump(mode="json"),
            timeframe_minutes=model.timeframe_minutes,
            benchmark_symbol=model.benchmark_symbol,
            symbols=list(config.symbols),
            xgboost_params=dict(model.xgboost_params),
            seed=training.seed,
            train_start=training.train_start,
            train_end=training.train_end,
            validation_start=training.validation_start,
            validation_end=training.validation_end,
            train_rows=train.complete_rows,
            validation_rows=validation.complete_rows,
            metrics=result.metrics,
            versions={
                "python": platform.python_version(),
                "carcharoth": _package_version("carcharoth"),
                "xgboost": xgboost.__version__,
            },
            created_at=created_at,
        )
        (temporary / MANIFEST_FILENAME).write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        report = {
            "train": train_diagnostics,
            "validation": validation_diagnostics,
            "metrics": result.metrics,
            "feature_importance_gain": result.feature_importance,
            "best_iteration": result.best_iteration,
        }
        (temporary / REPORT_FILENAME).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n"
        )
        os.replace(temporary, target_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target_dir
