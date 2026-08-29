"""Pure selection of one directional entry opportunity or abstention."""

import math
from dataclasses import dataclass

from carcharoth.domain.models import EntryCandidate, PositionDirection


@dataclass(frozen=True, slots=True)
class ArbitrationResult:
    selected: EntryCandidate | None
    reason: str
    indicators: dict[str, float]


class DirectionalArbiter:
    """Compare normalized candidates from one strategy family.

    Candidate-native indicators never affect selection. Invalid candidate
    scores are conservatively normalized to zero.
    """

    def __init__(
        self,
        shorting_enabled: bool = False,
        min_entry_score: float = 0.5,
        min_score_margin: float = 0.1,
    ) -> None:
        self._shorting_enabled = shorting_enabled
        self._min_entry_score = min_entry_score
        self._min_score_margin = min_score_margin

    def select(
        self,
        long: EntryCandidate | None,
        short: EntryCandidate | None,
    ) -> ArbitrationResult:
        long_score = self._score(long, PositionDirection.LONG)
        short_score = self._score(short, PositionDirection.SHORT) if self._shorting_enabled else 0.0
        indicators = {
            "long_score": long_score,
            "short_score": short_score,
            "score_margin": abs(long_score - short_score),
            "min_entry_score": self._min_entry_score,
            "min_score_margin": self._min_score_margin,
        }

        if long_score >= short_score:
            selected, winner_score, loser_score = long, long_score, short_score
        else:
            selected, winner_score, loser_score = short, short_score, long_score

        if selected is None or winner_score < self._min_entry_score:
            return ArbitrationResult(
                None,
                f"no entry clears minimum score {self._min_entry_score:.2f}",
                indicators,
            )
        margin = winner_score - loser_score
        if margin < self._min_score_margin:
            return ArbitrationResult(
                None,
                f"directional scores too close ({margin:.2f} < {self._min_score_margin:.2f})",
                indicators,
            )
        return ArbitrationResult(
            selected,
            f"selected {selected.direction.value} score {winner_score:.2f} over {loser_score:.2f}",
            indicators,
        )

    @staticmethod
    def _score(candidate: EntryCandidate | None, expected_direction: PositionDirection) -> float:
        if candidate is None or candidate.direction is not expected_direction:
            return 0.0
        score = candidate.score
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            return 0.0
        return score
