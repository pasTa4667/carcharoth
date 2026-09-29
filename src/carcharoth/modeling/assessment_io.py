"""Local-first assessment audit trail; optional dashboard publication is best effort."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from carcharoth.modeling.eligibility import EligibilityReport

logger = logging.getLogger(__name__)
AssessmentPublisher = Callable[[EligibilityReport], None]


class EligibilityError(ValueError):
    def __init__(self, report: EligibilityReport, path: Path) -> None:
        self.report, self.path = report, path
        reasons = sorted(
            {reason.code for reason in report.global_reasons}
            | {reason.code for symbol in report.symbols for reason in symbol.reasons}
        )
        super().__init__(
            f"training eligibility rejected ({', '.join(reasons)}); "
            f"assessment={report.assessment_id}; report={path}"
        )


def persist_assessment(
    report: EligibilityReport,
    path: Path,
    publish: AssessmentPublisher | None = None,
) -> None:
    report.updated_at = datetime.now(UTC)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w") as handle:
            json.dump(report.payload(), handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    if publish is not None:
        try:
            publish(report)
        except Exception as exc:
            logger.warning(
                "assessment %s remains in %s; Grafana publication failed: %s",
                report.assessment_id,
                path,
                exc,
            )


def log_assessment(report: EligibilityReport, path: Path) -> None:
    for symbol in report.symbols:
        if symbol.reasons:
            logger.warning(
                "assessment=%s symbol=%s decision=%s reasons=%s",
                report.assessment_id,
                symbol.symbol,
                symbol.decision,
                json.dumps([reason.model_dump() for reason in symbol.reasons]),
            )
    logger.warning(
        "%s assessment=%s status=%s requested=%d retained=%d excluded=%d report=%s",
        report.scope,
        report.assessment_id,
        report.status,
        len(report.requested_symbols),
        len(report.effective_symbols),
        sum(s.decision == "excluded" for s in report.symbols),
        path,
    )
