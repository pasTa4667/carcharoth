"""Report-only replay diagnostics. Never change bars, membership or positions."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import UUID

from carcharoth.config.loader import config_hash
from carcharoth.config.run_config import RunConfig
from carcharoth.domain.models import Bar
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.features.session import CalendarFetcher
from carcharoth.modeling.artifact import MANIFEST_FILENAME, load_manifest
from carcharoth.modeling.assessment_io import (
    AssessmentPublisher,
    log_assessment,
    persist_assessment,
)
from carcharoth.modeling.dataset import prepare_dataset, window_evidence
from carcharoth.modeling.eligibility import EligibilityReport, SymbolAssessment, assess
from carcharoth.services.alpaca.historical import warmup_window


def report_backtest_eligibility(
    config: RunConfig,
    bars: Mapping[str, Sequence[Bar]],
    symbols: Sequence[str],
    start: datetime,
    end_exclusive: datetime,
    run_id: UUID,
    fetch_calendar: CalendarFetcher | None,
    output_dir: Path,
    publish: AssessmentPublisher | None = None,
    loop_context: dict[str, object] | None = None,
) -> EligibilityReport:
    pipeline = FeaturePipeline(
        config.model.features, config.model.timeframe_minutes, config.model.benchmark_symbol
    )
    report = EligibilityReport(
        scope="backtest",
        config_hash=config_hash(config),
        policy=config.training.eligibility,
        feature_schema=pipeline.schema_hash,
        horizon_bars=0,
        timeframe_minutes=config.model.timeframe_minutes,
        universe_metadata=config.universe_metadata,
        requested_symbols=list(symbols),
        run_id=run_id,
        loop_context=loop_context,
        windows={"backtest": {"start": start, "end_exclusive": end_exclusive}},
    )
    try:
        if fetch_calendar is None:
            raise ValueError("exchange calendar unavailable; data coverage was not assessed")
        sessions = fetch_calendar(
            (start - warmup_window(pipeline.required_bars())).date(), end_exclusive.date()
        )
        if not sessions:
            raise ValueError("exchange calendar contains no sessions")
        artifact = config.model.artifact_path
        manifest = load_manifest(artifact / MANIFEST_FILENAME) if artifact is not None else None
        report.model_id = manifest.model_id if manifest else None
        members = manifest.effective_symbols if manifest else None
        requested = list(
            dict.fromkeys(
                [*symbols, *([pipeline.benchmark_symbol] if pipeline.requires_benchmark else [])]
            )
        )
        prepared = prepare_dataset(bars, requested, pipeline, sessions)
        assessments = []
        for symbol in symbols:
            evidence = window_evidence(
                prepared[symbol], start, end_exclusive, config.model.timeframe_minutes, 0, sessions
            )
            # Inference needs features, never tomorrow's entry/exit label.
            evidence.usable_rows = evidence.feature_ready_rows
            evidence.rejections = {
                "incomplete_features": evidence.attempted_rows - evidence.feature_ready_rows
            }
            assessments.append(
                SymbolAssessment(
                    symbol=symbol,
                    windows={"backtest": evidence},
                    trained=symbol in members if members is not None else None,
                )
            )
        if pipeline.requires_benchmark:
            benchmark = prepared[pipeline.benchmark_symbol]
            benchmark = replace(benchmark, matrix=benchmark.matrix[:, pipeline.benchmark_columns])
            evidence = window_evidence(
                benchmark, start, end_exclusive, config.model.timeframe_minutes, 0, sessions
            )
            evidence.usable_rows = evidence.feature_ready_rows
            report.context[pipeline.benchmark_symbol] = {"backtest": evidence}
        report = assess(report, assessments)
    except Exception as exc:
        report.status = "error"
        report.error = str(exc)
    report.outcome = "reported"
    path = output_dir / f"{run_id}.eligibility.json"
    persist_assessment(report, path, publish)
    log_assessment(report, path)
    return report
