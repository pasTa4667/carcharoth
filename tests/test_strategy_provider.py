"""Strategy provider dispatch: regime evaluation cadence, persistence and
hold-until-flat switching."""

from datetime import UTC, datetime, timedelta

import pytest

from carcharoth.domain.models import BarSpec, Timeframe
from carcharoth.engine.strategy_provider import RegimeStrategyProvider, SingleStrategyProvider
from carcharoth.regime.models import Regime, RegimeAssessment, StrategyAssignment
from tests.factories import BASE_TIME, make_bars, make_position
from tests.fakes import (
    FakeDetector,
    FakeStrategy,
    InMemoryRegimeEvaluationRepository,
    InMemoryStrategyAssignmentRepository,
)

AS_OF = datetime(2026, 7, 8, 15, 0, tzinfo=UTC)
BARS = make_bars([100.0, 101.0, 102.0])


class TrendStrategy(FakeStrategy):
    name = "trend_fake"


class ReversionStrategy(FakeStrategy):
    name = "reversion_fake"


class DailyStrategy(FakeStrategy):
    name = "daily_fake"

    def required_bars(self) -> BarSpec:
        return BarSpec(Timeframe.daily(), 20)


def make_assessment(regime: Regime, symbol: str = "AAPL", score: float = 0.5) -> RegimeAssessment:
    return RegimeAssessment(
        symbol=symbol,
        regime=regime,
        score=score if regime is Regime.TRENDING else -score,
        directional_score=score if regime is Regime.TRENDING else -score,
        stability=1.0,
        evidence=(),
    )


def make_prob_assessment(
    regime: Regime, confidence: float, symbol: str = "AAPL"
) -> RegimeAssessment:
    """A probabilistic (HMM-style) assessment: score == top probability."""
    return RegimeAssessment(
        symbol=symbol,
        regime=regime,
        score=confidence,
        directional_score=0.0,
        stability=1.0,
        evidence=(),
        probabilities={regime: confidence},
    )


def make_provider(
    detector: FakeDetector | None = None,
    assignments_repo: InMemoryStrategyAssignmentRepository | None = None,
    evaluations_repo: InMemoryRegimeEvaluationRepository | None = None,
    evaluate_interval_minutes: int = 1,
    trend_lookback: int = 20,
    min_confidence: float | None = None,
) -> tuple[
    RegimeStrategyProvider,
    FakeDetector,
    InMemoryRegimeEvaluationRepository,
    InMemoryStrategyAssignmentRepository,
    dict[Regime, FakeStrategy],
]:
    detector = detector if detector is not None else FakeDetector()
    evaluations_repo = evaluations_repo or InMemoryRegimeEvaluationRepository()
    assignments_repo = assignments_repo or InMemoryStrategyAssignmentRepository()
    strategies: dict[Regime, FakeStrategy] = {
        Regime.TRENDING: TrendStrategy({}, lookback=trend_lookback),
        Regime.MEAN_REVERTING: ReversionStrategy({}),
    }
    provider = RegimeStrategyProvider(
        detector=detector,
        strategies=strategies,
        evaluations_repo=evaluations_repo,
        assignments_repo=assignments_repo,
        evaluate_interval_minutes=evaluate_interval_minutes,
        default_regime=Regime.MEAN_REVERTING,
        min_confidence=min_confidence,
    )
    return provider, detector, evaluations_repo, assignments_repo, strategies


def test_single_provider_always_returns_its_strategy() -> None:
    strategy = FakeStrategy({})
    provider = SingleStrategyProvider(strategy)
    assert provider.required_bars() == strategy.required_bars()
    assert provider.resolve("AAPL", BARS, None, AS_OF) is strategy


def test_first_resolve_assigns_detected_regime() -> None:
    detector = FakeDetector({"AAPL": [make_assessment(Regime.TRENDING)]})
    provider, _, evaluations, assignments, strategies = make_provider(detector)

    assert provider.resolve("AAPL", BARS, None, AS_OF) is strategies[Regime.TRENDING]
    assert len(evaluations.saved) == 1
    assert [a.strategy for a in assignments.saved] == ["trend_fake"]
    assert assignments.saved[0].since == AS_OF


def test_warmup_detector_falls_back_to_default_regime() -> None:
    provider, _, evaluations, assignments, strategies = make_provider()

    assert provider.resolve("AAPL", BARS, None, AS_OF) is strategies[Regime.MEAN_REVERTING]
    assert evaluations.saved == []
    assert [a.strategy for a in assignments.saved] == ["reversion_fake"]


def test_regime_flip_switches_immediately_when_flat() -> None:
    detector = FakeDetector(
        {"AAPL": [make_assessment(Regime.MEAN_REVERTING), make_assessment(Regime.TRENDING)]}
    )
    provider, _, _, assignments, strategies = make_provider(detector)

    assert provider.resolve("AAPL", BARS, None, AS_OF) is strategies[Regime.MEAN_REVERTING]
    as_of = AS_OF + timedelta(minutes=1)
    assert provider.resolve("AAPL", BARS, None, as_of) is strategies[Regime.TRENDING]
    assert [a.strategy for a in assignments.saved] == ["reversion_fake", "trend_fake"]


def test_regime_flip_holds_until_flat_with_open_position() -> None:
    detector = FakeDetector(
        {"AAPL": [make_assessment(Regime.MEAN_REVERTING), make_assessment(Regime.TRENDING)]}
    )
    provider, _, _, assignments, strategies = make_provider(detector)
    position = make_position("AAPL")

    assert provider.resolve("AAPL", BARS, position, AS_OF) is strategies[Regime.MEAN_REVERTING]
    # regime flips to trending, but the position is still open
    as_of = AS_OF + timedelta(minutes=1)
    assert provider.resolve("AAPL", BARS, position, as_of) is strategies[Regime.MEAN_REVERTING]
    assert len(assignments.saved) == 1
    # once flat, the pending regime takes over
    as_of = AS_OF + timedelta(minutes=2)
    assert provider.resolve("AAPL", BARS, None, as_of) is strategies[Regime.TRENDING]
    assert [a.strategy for a in assignments.saved] == ["reversion_fake", "trend_fake"]


def test_restart_resumes_persisted_assignment() -> None:
    seeded = InMemoryStrategyAssignmentRepository(
        {
            "AAPL": StrategyAssignment(
                symbol="AAPL", strategy="trend_fake", regime=Regime.TRENDING, since=BASE_TIME
            )
        }
    )
    provider, _, _, assignments, strategies = make_provider(assignments_repo=seeded)

    # detector has no script (returns None): the persisted assignment holds
    assert provider.resolve("AAPL", BARS, None, AS_OF) is strategies[Regime.TRENDING]
    assert assignments.saved == []


def test_restart_drops_assignment_to_unmapped_strategy() -> None:
    seeded = InMemoryStrategyAssignmentRepository(
        {
            "AAPL": StrategyAssignment(
                symbol="AAPL", strategy="retired", regime=Regime.TRENDING, since=BASE_TIME
            )
        }
    )
    provider, _, _, assignments, strategies = make_provider(assignments_repo=seeded)

    assert provider.resolve("AAPL", BARS, None, AS_OF) is strategies[Regime.MEAN_REVERTING]
    assert [a.strategy for a in assignments.saved] == ["reversion_fake"]


def test_detector_runs_on_configured_cadence() -> None:
    detector = FakeDetector({"AAPL": [make_assessment(Regime.TRENDING)]})
    provider, detector, evaluations, _, _ = make_provider(detector, evaluate_interval_minutes=3)

    for minute in range(7):  # one resolve per market minute
        provider.resolve("AAPL", BARS, None, AS_OF + timedelta(minutes=minute))
    assert len(detector.calls) == 3  # minutes 0, 3 and 6
    assert len(evaluations.saved) == 3  # sticky assessment persisted each run


def test_cadence_is_tracked_per_symbol() -> None:
    provider, detector, _, _, _ = make_provider(evaluate_interval_minutes=2)

    provider.resolve("AAPL", BARS, None, AS_OF)
    provider.resolve("MSFT", BARS, None, AS_OF)
    provider.resolve("AAPL", BARS, None, AS_OF + timedelta(minutes=1))
    assert detector.calls == ["AAPL", "MSFT"]


def test_warmup_miss_still_advances_the_attempt_clock() -> None:
    """A warming-up detector is not retried before the interval elapses —
    expensive detectors must not refit on every tick."""
    provider, detector, _, _, _ = make_provider(evaluate_interval_minutes=5)

    provider.resolve("AAPL", BARS, None, AS_OF)  # assess -> None (no script)
    provider.resolve("AAPL", BARS, None, AS_OF + timedelta(minutes=1))
    provider.resolve("AAPL", BARS, None, AS_OF + timedelta(minutes=4))
    assert detector.calls == ["AAPL"]
    provider.resolve("AAPL", BARS, None, AS_OF + timedelta(minutes=5))
    assert detector.calls == ["AAPL", "AAPL"]


def test_required_bars_covers_detector_and_strategies() -> None:
    provider, _, _, _, _ = make_provider(FakeDetector(lookback=400))
    assert provider.required_bars() == BarSpec(Timeframe.minutes(5), 400)

    provider, _, _, _, _ = make_provider(FakeDetector(lookback=10), trend_lookback=50)
    assert provider.required_bars() == BarSpec(Timeframe.minutes(5), 50)


def test_rejects_mixed_strategy_timeframes() -> None:
    with pytest.raises(ValueError, match="share one timeframe"):
        RegimeStrategyProvider(
            detector=FakeDetector(),
            strategies={
                Regime.TRENDING: DailyStrategy({}),
                Regime.MEAN_REVERTING: ReversionStrategy({}),
            },
            evaluations_repo=InMemoryRegimeEvaluationRepository(),
            assignments_repo=InMemoryStrategyAssignmentRepository(),
        )


def test_rejects_default_regime_without_mapping() -> None:
    with pytest.raises(ValueError, match="default regime"):
        RegimeStrategyProvider(
            detector=FakeDetector(),
            strategies={Regime.TRENDING: TrendStrategy({})},
            evaluations_repo=InMemoryRegimeEvaluationRepository(),
            assignments_repo=InMemoryStrategyAssignmentRepository(),
            default_regime=Regime.MEAN_REVERTING,
        )


def test_strategies_still_resolve_between_detector_runs() -> None:
    """The cadence gates the detector, not strategy evaluation."""
    detector = FakeDetector({"AAPL": [make_assessment(Regime.TRENDING)]})
    provider, _, _, _, strategies = make_provider(detector, evaluate_interval_minutes=10)

    for minute in range(5):
        as_of = AS_OF + timedelta(minutes=minute)
        assert provider.resolve("AAPL", BARS, None, as_of) is strategies[Regime.TRENDING]
    assert len(detector.calls) == 1


def test_low_confidence_assessment_holds_previous_regime() -> None:
    detector = FakeDetector(
        {
            "AAPL": [
                make_prob_assessment(Regime.MEAN_REVERTING, confidence=0.9),
                make_prob_assessment(Regime.TRENDING, confidence=0.3),
                make_prob_assessment(Regime.TRENDING, confidence=0.8),
            ]
        }
    )
    provider, _, evaluations, _, strategies = make_provider(detector, min_confidence=0.5)

    assert provider.resolve("AAPL", BARS, None, AS_OF) is strategies[Regime.MEAN_REVERTING]
    # 0.3 < 0.5: the uncertain trending verdict is persisted but not acted on
    as_of = AS_OF + timedelta(minutes=1)
    assert provider.resolve("AAPL", BARS, None, as_of) is strategies[Regime.MEAN_REVERTING]
    assert [a.regime for a, _ in evaluations.saved] == [Regime.MEAN_REVERTING, Regime.TRENDING]
    # 0.8 >= 0.5: now the switch happens
    as_of = AS_OF + timedelta(minutes=2)
    assert provider.resolve("AAPL", BARS, None, as_of) is strategies[Regime.TRENDING]


def test_min_confidence_ignores_non_probabilistic_assessments() -> None:
    """Score-detector assessments (no probabilities) are never gated."""
    detector = FakeDetector({"AAPL": [make_assessment(Regime.TRENDING, score=0.1)]})
    provider, _, _, _, strategies = make_provider(detector, min_confidence=0.9)

    assert provider.resolve("AAPL", BARS, None, AS_OF) is strategies[Regime.TRENDING]
