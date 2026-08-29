import math

import pytest

from carcharoth.domain.models import EntryCandidate, PositionDirection
from carcharoth.strategies.arbitration import DirectionalArbiter


def candidate(direction: PositionDirection, score: float) -> EntryCandidate:
    return EntryCandidate(direction, score, f"{direction.value} setup", {"native": 42.0})


def arbiter(**overrides: float | bool) -> DirectionalArbiter:
    return DirectionalArbiter(**overrides)  # type: ignore[arg-type]


def test_selects_clear_long_winner_and_reports_only_comparable_scores() -> None:
    result = arbiter(shorting_enabled=True).select(
        candidate(PositionDirection.LONG, 0.8), candidate(PositionDirection.SHORT, 0.6)
    )
    assert result.selected is not None
    assert result.selected.direction is PositionDirection.LONG
    assert result.indicators["long_score"] == 0.8
    assert result.indicators["short_score"] == 0.6
    assert "native" not in result.indicators


def test_short_is_ineligible_when_disabled() -> None:
    result = arbiter(shorting_enabled=False).select(None, candidate(PositionDirection.SHORT, 1.0))
    assert result.selected is None
    assert result.indicators["short_score"] == 0.0


def test_abstains_below_absolute_threshold() -> None:
    result = arbiter(shorting_enabled=True, min_entry_score=0.5).select(
        candidate(PositionDirection.LONG, 0.49), None
    )
    assert result.selected is None
    assert "minimum score" in result.reason


def test_abstains_on_near_tie_and_exact_tie() -> None:
    close = arbiter(shorting_enabled=True, min_score_margin=0.1).select(
        candidate(PositionDirection.LONG, 0.8), candidate(PositionDirection.SHORT, 0.71)
    )
    tied = arbiter(shorting_enabled=True, min_score_margin=0.1).select(
        candidate(PositionDirection.LONG, 0.8), candidate(PositionDirection.SHORT, 0.8)
    )
    assert close.selected is None
    assert tied.selected is None
    assert "too close" in close.reason
    assert "too close" in tied.reason


@pytest.mark.parametrize("bad", [-0.1, 1.1, math.nan, math.inf, -math.inf])
def test_invalid_scores_are_ineligible(bad: float) -> None:
    result = arbiter(shorting_enabled=True).select(
        candidate(PositionDirection.LONG, bad), candidate(PositionDirection.SHORT, 0.6)
    )
    assert result.selected is not None
    assert result.selected.direction is PositionDirection.SHORT
    assert result.indicators["long_score"] == 0.0


def test_candidate_with_wrong_direction_is_ineligible() -> None:
    result = arbiter(shorting_enabled=True).select(candidate(PositionDirection.SHORT, 1.0), None)
    assert result.selected is None
    assert result.indicators["long_score"] == 0.0
