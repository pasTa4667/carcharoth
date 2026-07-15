"""Detector combination logic, tested with stub features."""

import numpy as np
import numpy.typing as npt
import pytest

from carcharoth.regime import Regime, build_feature
from carcharoth.regime.features.base import RegimeFeature
from carcharoth.regime.models import Evidence
from carcharoth.regime.score_detector import ScoreRegimeDetector
from tests.factories import make_bars


class StubFeature(RegimeFeature):
    def __init__(
        self,
        name: str,
        direction: float | None = None,
        stability: float | None = None,
        min_returns: int = 1,
    ) -> None:
        self.name = name
        self._direction = direction
        self._stability = stability
        self._min_returns = min_returns

    def min_returns(self) -> int:
        return self._min_returns

    def compute(self, log_returns: npt.NDArray[np.float64]) -> Evidence | None:
        if len(log_returns) < self._min_returns:
            return None
        return Evidence(
            feature=self.name, value=0.0, direction=self._direction, stability=self._stability
        )


BARS = make_bars([100.0, 101.0, 102.0, 101.0, 100.0])


def test_directional_scores_are_weight_averaged() -> None:
    detector = ScoreRegimeDetector(
        features=[
            (StubFeature("a", direction=1.0), 3.0),
            (StubFeature("b", direction=-1.0), 1.0),
        ],
        lookback=100,
    )
    assessment = detector.assess("AAPL", BARS)
    assert assessment is not None
    assert assessment.directional_score == pytest.approx(0.5)
    assert assessment.regime is Regime.TRENDING


def test_lowest_stability_attenuates_score() -> None:
    detector = ScoreRegimeDetector(
        features=[
            (StubFeature("a", direction=0.8), 1.0),
            (StubFeature("b", stability=0.9), 1.0),
            (StubFeature("c", stability=0.25), 1.0),
        ],
        lookback=100,
    )
    assessment = detector.assess("AAPL", BARS)
    assert assessment is not None
    assert assessment.stability == pytest.approx(0.25)
    assert assessment.score == pytest.approx(0.8 * 0.25)


def test_stability_defaults_to_one_without_change_features() -> None:
    detector = ScoreRegimeDetector(features=[(StubFeature("a", direction=-0.4), 1.0)], lookback=100)
    assessment = detector.assess("AAPL", BARS)
    assert assessment is not None
    assert assessment.stability == 1.0
    assert assessment.score == pytest.approx(-0.4)
    assert assessment.regime is Regime.MEAN_REVERTING


def test_zero_score_falls_to_mean_reversion() -> None:
    detector = ScoreRegimeDetector(features=[(StubFeature("a", direction=0.0), 1.0)], lookback=100)
    assessment = detector.assess("AAPL", BARS)
    assert assessment is not None
    assert assessment.regime is Regime.MEAN_REVERTING


def test_no_directional_evidence_returns_none() -> None:
    detector = ScoreRegimeDetector(features=[(StubFeature("a", stability=0.5), 1.0)], lookback=100)
    assert detector.assess("AAPL", BARS) is None


def test_warmed_up_features_are_skipped_not_fatal() -> None:
    detector = ScoreRegimeDetector(
        features=[
            (StubFeature("a", direction=1.0), 1.0),
            (StubFeature("b", direction=-1.0, min_returns=1000), 5.0),
        ],
        lookback=100,
    )
    assessment = detector.assess("AAPL", BARS)
    assert assessment is not None
    assert assessment.directional_score == pytest.approx(1.0)
    assert len(assessment.evidence) == 1


def test_too_few_bars_returns_none() -> None:
    detector = ScoreRegimeDetector(features=[(StubFeature("a", direction=1.0), 1.0)], lookback=100)
    assert detector.assess("AAPL", []) is None
    assert detector.assess("AAPL", make_bars([100.0])) is None


def test_required_lookback_covers_slowest_feature() -> None:
    detector = ScoreRegimeDetector(
        features=[(StubFeature("a", direction=1.0, min_returns=240), 1.0)], lookback=100
    )
    assert detector.required_lookback() == 241

    detector = ScoreRegimeDetector(
        features=[(StubFeature("a", direction=1.0, min_returns=240), 1.0)], lookback=400
    )
    assert detector.required_lookback() == 400


def test_assessment_carries_all_evidence() -> None:
    detector = ScoreRegimeDetector(
        features=[
            (StubFeature("a", direction=1.0), 1.0),
            (StubFeature("b", stability=0.5), 1.0),
        ],
        lookback=100,
    )
    assessment = detector.assess("AAPL", BARS)
    assert assessment is not None
    assert [e.feature for e in assessment.evidence] == ["a", "b"]
    # the detector stamps its configured weight onto each evidence
    assert [e.weight for e in assessment.evidence] == [1.0, 1.0]


def test_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError):
        ScoreRegimeDetector(features=[], lookback=100)
    with pytest.raises(ValueError):
        ScoreRegimeDetector(features=[(StubFeature("a", direction=1.0), 0.0)], lookback=100)


def test_build_feature_resolves_registered_names() -> None:
    feature = build_feature("hurst", {"min_window": 16})
    assert feature.name == "hurst"
    assert feature.min_returns() == 128


def test_build_feature_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown regime feature"):
        build_feature("nope", {})


def test_end_to_end_with_real_features_on_trending_prices() -> None:
    rng = np.random.default_rng(7)
    steps = rng.normal(0.001, 0.005, 600)
    prices = 100.0 * np.exp(np.cumsum(steps))
    detector = ScoreRegimeDetector(
        features=[(build_feature("hurst", {}), 1.0), (build_feature("cusum", {}), 1.0)],
        lookback=400,
    )
    assessment = detector.assess("AAPL", make_bars(list(prices)))
    assert assessment is not None
    assert {e.feature for e in assessment.evidence} == {"hurst", "cusum"}
