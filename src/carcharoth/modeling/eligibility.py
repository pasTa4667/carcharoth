"""Pure, versioned evidence and cohort selection for offline market data."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from carcharoth.config.app_config import EligibilityConfig
from carcharoth.config.strict import StrictModel
from carcharoth.config.universe_config import UniverseMetadata, members_digest


class Reason(StrictModel):
    code: str
    split: str
    observed: float | None = None
    required: float | None = None


class WindowEvidence(StrictModel):
    start: datetime
    end_exclusive: datetime
    raw_bars: int = 0
    scheduled_bars: int = 0
    expected_rows: int = 0
    attempted_rows: int = 0
    feature_ready_rows: int = 0
    usable_rows: int = 0
    first_bar: datetime | None = None
    last_bar: datetime | None = None
    missing_sessions: int = 0
    longest_missing_session_run: int = 0
    rejections: dict[str, int] = Field(default_factory=dict)
    input_errors: dict[str, int] = Field(default_factory=dict)

    @property
    def usable_coverage(self) -> float:
        return self.usable_rows / self.expected_rows if self.expected_rows else 0.0

    @property
    def raw_coverage(self) -> float:
        return self.raw_bars / self.scheduled_bars if self.scheduled_bars else 0.0


class SymbolAssessment(StrictModel):
    symbol: str
    decision: Literal["retained", "excluded", "warning", "not_assessed"] = "retained"
    windows: dict[str, WindowEvidence] = Field(default_factory=dict)
    reasons: list[Reason] = Field(default_factory=list)
    trained: bool | None = None


class EligibilityReport(StrictModel):
    schema_version: Literal[1] = 1
    assessment_id: UUID = Field(default_factory=uuid4)
    scope: Literal["training", "backtest"] = "training"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    config_hash: str
    status: Literal["passed", "passed_with_exclusions", "rejected", "error", "warning"] = "error"
    outcome: Literal["pending", "published", "failed", "reported"] = "pending"
    model_id: str | None = None
    run_id: UUID | None = None
    loop_context: dict[str, object] | None = None
    policy: EligibilityConfig
    feature_schema: str
    horizon_bars: int
    timeframe_minutes: int = Field(default=5, gt=0)
    universe_metadata: UniverseMetadata | None = None
    requested_symbols: list[str]
    windows: dict[str, dict[str, datetime]] = Field(default_factory=dict)
    effective_symbols: list[str] = Field(default_factory=list)
    symbols: list[SymbolAssessment] = Field(default_factory=list)
    context: dict[str, dict[str, WindowEvidence]] = Field(default_factory=dict)
    global_reasons: list[Reason] = Field(default_factory=list)
    error: str | None = None
    reference_data: dict[str, str] = Field(
        default_factory=lambda: {
            "listing_as_of": "unknown/not_available",
            "corporate_actions": "unknown/not_available",
            "feed": "provider_default",
            "adjustment": "provider_default",
            "asof": "provider_default",
            "limitations": "Missing bars do not establish delisting, merger or halt. "
            "Validation-availability selection conditions the training cohort. "
            "Cached history may have normalized upstream duplicates.",
        }
    )

    def payload(self) -> dict[str, object]:
        result = self.model_dump(mode="json")
        result["requested_digest"] = members_digest(self.requested_symbols)
        result["effective_digest"] = members_digest(self.effective_symbols)
        for symbol, payload in zip(self.symbols, result["symbols"], strict=True):
            for split, evidence in symbol.windows.items():
                payload["windows"][split].update(
                    usable_coverage=evidence.usable_coverage, raw_coverage=evidence.raw_coverage
                )
        return result


def window_reasons(split: str, evidence: WindowEvidence, policy: EligibilityConfig) -> list[Reason]:
    reasons = [
        Reason(code=code, split=split, observed=count, required=0)
        for code, count in sorted(evidence.input_errors.items())
        if count
    ]
    if not evidence.raw_bars:
        reasons.append(Reason(code="no_bars", split=split, observed=0, required=1))
    minimum = policy.min_train_rows if split == "train" else policy.min_validation_rows
    if split != "backtest" and evidence.usable_rows < minimum:
        reasons.append(
            Reason(
                code=f"insufficient_{split}_rows",
                split=split,
                observed=evidence.usable_rows,
                required=minimum,
            )
        )
    if evidence.usable_coverage < policy.min_usable_coverage:
        reasons.append(
            Reason(
                code="insufficient_usable_coverage",
                split=split,
                observed=evidence.usable_coverage,
                required=policy.min_usable_coverage,
            )
        )
    return reasons


def assess(report: EligibilityReport, symbols: Sequence[SymbolAssessment]) -> EligibilityReport:
    """Select one training cohort; backtests only annotate, never select."""
    report = report.model_copy(deep=True)
    report.symbols = [symbol.model_copy(deep=True) for symbol in symbols]
    report.effective_symbols = []
    for symbol in report.symbols:
        symbol.reasons = [
            reason
            for split, evidence in symbol.windows.items()
            for reason in window_reasons(split, evidence, report.policy)
        ]
        symbol.decision = (
            ("warning" if report.scope == "backtest" else "excluded")
            if symbol.reasons
            else "retained"
        )
        if not symbol.reasons or report.scope == "backtest":
            report.effective_symbols.append(symbol.symbol)
    for context_symbol, windows in report.context.items():
        for split, evidence in windows.items():
            if (
                evidence.input_errors
                or evidence.usable_coverage < report.policy.min_usable_coverage
            ):
                report.global_reasons.append(
                    Reason(
                        code=f"unusable_benchmark:{context_symbol}",
                        split=split,
                        observed=evidence.usable_coverage,
                        required=report.policy.min_usable_coverage,
                    )
                )
    if report.scope == "backtest":
        report.status = (
            "warning"
            if report.global_reasons or any(symbol.reasons for symbol in report.symbols)
            else "passed"
        )
        return report
    if report.global_reasons:
        # Shared context failure is not evidence that every stock is bad.
        report.status = "rejected"
        report.effective_symbols = []
        for symbol in report.symbols:
            symbol.decision = "not_assessed"
            symbol.reasons = []
        return report
    required = max(
        report.policy.min_symbols,
        math.ceil(len(report.requested_symbols) * report.policy.min_retained_fraction),
    )
    if len(report.effective_symbols) < required:
        report.global_reasons.append(
            Reason(
                code="insufficient_retained_symbols",
                split="all",
                observed=len(report.effective_symbols),
                required=required,
            )
        )
    dropped = len(report.effective_symbols) != len(report.requested_symbols)
    report.status = (
        "rejected"
        if report.global_reasons or (dropped and report.policy.mode == "strict")
        else "passed_with_exclusions"
        if dropped
        else "passed"
    )
    return report
