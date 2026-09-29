from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from carcharoth.config.app_config import EligibilityConfig
from carcharoth.features.session import MARKET_TZ, TradingSession, scheduled_bars
from carcharoth.modeling.eligibility import (
    EligibilityReport,
    SymbolAssessment,
    WindowEvidence,
    assess,
)


def calendar(day=2, month=1, close_hour=16):
    return TradingSession(
        datetime(2024, month, day, 9, 30, tzinfo=MARKET_TZ),
        datetime(2024, month, day, close_hour, tzinfo=MARKET_TZ),
    )


def evidence(rows=100, expected=100):
    return WindowEvidence(
        start=datetime(2024, 1, 2, tzinfo=UTC),
        end_exclusive=datetime(2024, 1, 3, tzinfo=UTC),
        raw_bars=rows,
        usable_rows=rows,
        expected_rows=expected,
    )


def report(**policy):
    return EligibilityReport(
        config_hash="hash",
        policy=EligibilityConfig(**policy),
        feature_schema="schema",
        horizon_bars=5,
        requested_symbols=["GOOD", "APC"],
    )


def test_calendar_early_close_dst_and_holidays():
    sessions = [calendar(8, 3), calendar(11, 3), calendar(29, 11, 13)]
    grid = scheduled_bars(sessions, 5)
    assert len(grid) == 78 * 2 + 42
    assert min(grid).astimezone(UTC).hour == 14
    assert sessions[1].open.astimezone(UTC).hour == 13
    assert not any(t.day in (9, 10) for t in grid if t.month == 3)
    assert len(scheduled_bars([calendar(29, 11, 13)], 7)) == 30


def test_drop_strict_and_retained_floor():
    symbols = [
        SymbolAssessment(symbol="GOOD", windows={"train": evidence(), "validation": evidence()}),
        SymbolAssessment(symbol="APC", windows={"train": evidence(), "validation": evidence(0)}),
    ]
    result = assess(report(), symbols)
    assert result.status == "passed_with_exclusions"
    assert result.effective_symbols == ["GOOD"]
    assert result.symbols[1].reasons[0].code == "no_bars"
    assert assess(report(mode="strict"), symbols).status == "rejected"
    assert assess(report(min_retained_fraction=0.51), symbols).status == "rejected"
    assert assess(report(), []).status == "rejected"


def test_threshold_boundary_and_validation():
    symbols = [
        SymbolAssessment(
            symbol=s, windows={"train": evidence(100, 125), "validation": evidence(30, 37)}
        )
        for s in ("GOOD", "APC")
    ]
    assert assess(report(), symbols).status == "passed"
    assert assess(report(min_usable_coverage=0.81), symbols).status == "rejected"
    with pytest.raises(ValidationError):
        EligibilityConfig(min_train_rows=0)
