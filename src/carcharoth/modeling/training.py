"""Offline global dataset -> immutable XGBoost artifact orchestration."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

import xgboost

from carcharoth.config.run_config import RunConfig
from carcharoth.domain.models import ModelTask
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.features.session import CalendarFetcher
from carcharoth.interfaces.optimization import BarsFetcher
from carcharoth.modeling.artifact import (
    MANIFEST_FILENAME,
    MODEL_FILENAME,
    REPORT_FILENAME,
    ArtifactManifest,
    file_sha256,
)
from carcharoth.modeling.assessment_io import (
    AssessmentPublisher,
    EligibilityError,
    log_assessment,
    persist_assessment,
)
from carcharoth.modeling.dataset import (
    ModelDataset,
    dataset_from_prepared,
    prepare_dataset,
    window_evidence,
)
from carcharoth.modeling.diagnostics import dataset_diagnostics, regression_diagnostics
from carcharoth.modeling.eligibility import EligibilityReport, Reason, SymbolAssessment, assess
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
    fetch_calendar: CalendarFetcher,
    publish_assessment: AssessmentPublisher | None = None,
    *,
    loop_context: dict[str, object] | None = None,
) -> Path:
    model = config.model
    training = config.training
    pipeline = FeaturePipeline(
        model.features,
        model.timeframe_minutes,
        model.benchmark_symbol,
    )
    report = EligibilityReport(
        config_hash=config_hash,
        policy=training.eligibility,
        feature_schema=pipeline.schema_hash,
        horizon_bars=model.target.horizon_bars,
        timeframe_minutes=model.timeframe_minutes,
        universe_metadata=config.universe_metadata,
        requested_symbols=list(config.symbols),
        loop_context=loop_context,
        windows={
            split: {"start": _start(start), "end_exclusive": _end_exclusive(end)}
            for split, start, end in (
                ("train", training.train_start, training.train_end),
                ("validation", training.validation_start, training.validation_end),
            )
        },
    )
    report_path = training.artifact_root / "_assessments" / f"{report.assessment_id}.json"
    try:
        symbols = list(
            dict.fromkeys(
                [
                    *config.symbols,
                    *([model.benchmark_symbol] if pipeline.requires_benchmark else []),
                ]
            )
        )
        spec = pipeline.required_bars()
        fetch_start = _start(training.train_start) - warmup_window(spec)
        fetch_end = _end_exclusive(training.validation_end)
        sessions = fetch_calendar(fetch_start.date(), training.validation_end)
        if not sessions:
            raise ValueError("training calendar contains no exchange sessions")
        bars = fetch_bars(symbols, spec.timeframe, fetch_start, fetch_end)
        # Never let an inclusive provider response introduce post-validation evidence.
        bars = {
            symbol: [bar for bar in values if fetch_start <= bar.timestamp < fetch_end]
            for symbol, values in bars.items()
        }
        prepared = prepare_dataset(bars, symbols, pipeline, sessions)
        datasets = {
            split: dataset_from_prepared(
                prepared,
                config.symbols,
                pipeline,
                model.target,
                _start(start),
                _end_exclusive(end),
                sessions,
            )
            for split, start, end in (
                ("train", training.train_start, training.train_end),
                ("validation", training.validation_start, training.validation_end),
            )
        }
        for split, dataset in datasets.items():
            if not any(e.expected_rows for e in dataset.evidence.values()):
                report.global_reasons.append(
                    Reason(
                        code="no_target_opportunities",
                        split=split,
                        observed=0,
                        required=1,
                    )
                )
        if pipeline.requires_benchmark:
            benchmark = prepared[model.benchmark_symbol]
            benchmark = replace(benchmark, matrix=benchmark.matrix[:, pipeline.benchmark_columns])
            context = {}
            for split, dataset in datasets.items():
                window = next(iter(dataset.evidence.values()))
                stats = window_evidence(
                    benchmark,
                    window.start,
                    window.end_exclusive,
                    model.timeframe_minutes,
                    0,
                    sessions,
                )
                stats.usable_rows = stats.feature_ready_rows
                context[split] = stats
            report.context[model.benchmark_symbol] = context
        report = assess(
            report,
            [
                SymbolAssessment(
                    symbol=symbol,
                    windows={split: data.evidence[symbol] for split, data in datasets.items()},
                )
                for symbol in config.symbols
            ],
        )
        persist_assessment(report, report_path, publish_assessment)
        log_assessment(report, report_path)
        if report.status == "rejected":
            raise EligibilityError(report, report_path)
        artifact = _fit_publish(
            config,
            config_hash,
            pipeline,
            datasets["train"].select_symbols(report.effective_symbols),
            datasets["validation"].select_symbols(report.effective_symbols),
            report,
        )
    except Exception as exc:
        report.outcome = "failed"
        report.error = str(exc)
        # A failed audit write must not mask the original error, nor start fitting.
        try:
            persist_assessment(report, report_path, publish_assessment)
        except Exception:
            logging.getLogger(__name__).exception("could not persist assessment %s", report_path)
        raise
    # The immutable artifact is already valid; a later audit finalization failure
    # must not turn successful publication into a misleading training failure.
    try:
        persist_assessment(report, report_path, publish_assessment)
    except Exception:
        logging.getLogger(__name__).exception(
            "artifact published; could not finalize %s", report_path
        )
    return artifact


def _fit_publish(
    config: RunConfig,
    config_hash: str,
    pipeline: FeaturePipeline,
    train: ModelDataset,
    validation: ModelDataset,
    assessment: EligibilityReport,
) -> Path:
    model, training = config.model, config.training
    train_diagnostics = dataset_diagnostics(train)
    validation_diagnostics = dataset_diagnostics(validation)
    train_by_symbol = Counter(row.symbol for row in train.rows)
    validation_by_symbol = Counter(row.symbol for row in validation.rows)
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
            effective_symbols=list(assessment.effective_symbols),
            eligibility_assessment_id=str(assessment.assessment_id),
            universe_metadata=config.universe_metadata,
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
        assessment.model_id = model_id
        assessment.outcome = "published"
        report: dict[str, object] = {
            "model_id": model_id,
            "eligibility": assessment.payload(),
            "task": train.task.value,
            "validation_start": training.validation_start.isoformat(),
            "validation_end": training.validation_end.isoformat(),
            "target": model.target.model_dump(mode="json"),
            "train": train_diagnostics,
            "validation": validation_diagnostics,
            "rows_by_symbol": {
                "train": {symbol: train_by_symbol[symbol] for symbol in config.symbols},
                "validation": {symbol: validation_by_symbol[symbol] for symbol in config.symbols},
            },
            "metrics": result.metrics,
            "feature_importance_gain": result.feature_importance,
            "best_iteration": result.best_iteration,
        }
        if train.task is ModelTask.REGRESSION:
            report["diagnostics"] = regression_diagnostics(
                validation,
                result.validation_predictions,
                result.feature_importance,
                result.permutation_importance,
            )
        (temporary / REPORT_FILENAME).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n"
        )
        os.replace(temporary, target_dir)
    except Exception:
        assessment.model_id = None
        assessment.outcome = "failed"
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target_dir
