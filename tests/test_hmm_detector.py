"""HMM regime detector: end-to-end classification on synthetic phases,
warm-up, determinism, refit cadence and fit-failure fallback."""

from datetime import UTC, datetime

import numpy as np
import pytest
from hmmlearn.hmm import GaussianHMM

from carcharoth.regime.hmm.detector import HmmRegimeDetector
from carcharoth.regime.models import Regime
from tests.factories import make_bars

# Strongly separated synthetic phases (drift, sigma per 5-min bar):
PHASES = {
    "up": (0.002, 0.002),
    "down": (-0.002, 0.002),
    "calm": (0.0, 0.0005),
    "wild": (0.0, 0.012),
}
SEGMENT = 250
TRAINING_WINDOW = 4 * SEGMENT


def make_detector(**overrides: object) -> HmmRegimeDetector:
    params: dict[str, object] = {
        "training_window": TRAINING_WINDOW,
        "vol_window": 10,
        "ema_period": 20,
        "adx_period": 10,
        "refit_interval_bars": 78,
        "seed": 42,
    }
    params.update(overrides)
    return HmmRegimeDetector(**params)  # type: ignore[arg-type]


def phase_prices(order: list[str], seed: int = 7) -> list[float]:
    """Concatenated random-walk segments; the first is padded so the total
    covers the detector's required lookback."""
    rng = np.random.default_rng(seed)
    steps: list[np.ndarray] = []
    for i, name in enumerate(order):
        drift, sigma = PHASES[name]
        length = SEGMENT + 60 if i == 0 else SEGMENT
        steps.append(rng.normal(drift, sigma, length))
    return list(100.0 * np.exp(np.cumsum(np.concatenate(steps))))


EXPECTED = {
    "up": Regime.TRENDING_UP,
    "down": Regime.TRENDING_DOWN,
    "calm": Regime.RANGE_BOUND,
    "wild": Regime.HIGH_VOLATILITY,
}


@pytest.mark.parametrize("last_phase", ["up", "down", "calm", "wild"])
def test_classifies_the_current_phase(last_phase: str) -> None:
    order = [name for name in PHASES if name != last_phase] + [last_phase]
    detector = make_detector()
    bars = make_bars(phase_prices(order), hl_range=0.3)

    assessment = detector.assess("AAPL", bars)
    assert assessment is not None
    assert assessment.probabilities is not None
    assert sum(assessment.probabilities.values()) == pytest.approx(1.0)
    assert assessment.regime is EXPECTED[last_phase]
    assert assessment.score > 0.5
    assert assessment.score == pytest.approx(assessment.probabilities[assessment.regime])


def test_assessment_carries_raw_feature_evidence() -> None:
    detector = make_detector()
    bars = make_bars(phase_prices(["calm", "wild", "down", "up"]), hl_range=0.3)
    assessment = detector.assess("AAPL", bars)
    assert assessment is not None
    assert [e.feature for e in assessment.evidence] == [
        "log_return",
        "volatility",
        "ema_distance",
        "adx",
    ]
    adx = assessment.evidence[3].value
    assert 0 <= adx <= 100  # raw units, not standardized


def test_warmup_returns_none_until_required_lookback() -> None:
    detector = make_detector()
    prices = phase_prices(["up", "down", "calm", "wild"])
    required = detector.required_lookback()
    assert required == TRAINING_WINDOW + max(10 + 1, 20, 2 * 10) + 1
    assert detector.assess("AAPL", make_bars(prices[: required - 1])) is None
    assert detector.assess("AAPL", make_bars(prices[:required], hl_range=0.3)) is not None


def test_same_seed_is_deterministic() -> None:
    bars = make_bars(phase_prices(["calm", "up", "wild", "down"]), hl_range=0.3)
    first = make_detector(seed=11).assess("AAPL", bars)
    second = make_detector(seed=11).assess("AAPL", bars)
    assert first is not None and second is not None
    assert first.regime is second.regime
    assert first.probabilities == second.probabilities


def test_refits_only_after_interval_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    detector = make_detector(refit_interval_bars=10)
    fits: list[str] = []
    original = HmmRegimeDetector._fit

    def counting_fit(
        self: HmmRegimeDetector, symbol: str, *args: object, **kwargs: object
    ) -> object:
        fits.append(symbol)
        return original(self, symbol, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(HmmRegimeDetector, "_fit", counting_fit)
    bars = make_bars(phase_prices(["up", "down", "calm", "wild"]), hl_range=0.3)
    required = detector.required_lookback()

    detector.assess("AAPL", bars[:required])
    assert fits == ["AAPL"]
    detector.assess("AAPL", bars[: required + 5])  # 5 new bars < 10
    assert fits == ["AAPL"]
    detector.assess("AAPL", bars[: required + 10])  # 10 new bars -> refit
    assert fits == ["AAPL", "AAPL"]


def test_fit_failure_keeps_previous_model(monkeypatch: pytest.MonkeyPatch) -> None:
    detector = make_detector(refit_interval_bars=5)
    bars = make_bars(phase_prices(["up", "down", "calm", "wild"]), hl_range=0.3)
    required = detector.required_lookback()

    first = detector.assess("AAPL", bars[:required])
    assert first is not None

    def broken_fit(self: HmmRegimeDetector, *args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(HmmRegimeDetector, "_fit", broken_fit)
    # refit is due but fails: the previous model still assesses
    assessment = detector.assess("AAPL", bars[: required + 5])
    assert assessment is not None


def test_fit_failure_without_previous_model_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector = make_detector()

    def broken_fit(self: HmmRegimeDetector, *args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(HmmRegimeDetector, "_fit", broken_fit)
    bars = make_bars(phase_prices(["up", "down", "calm", "wild"]), hl_range=0.3)
    assert detector.assess("AAPL", bars) is None


def test_models_are_kept_per_symbol() -> None:
    detector = make_detector()
    up_bars = make_bars(phase_prices(["down", "calm", "wild", "up"]), hl_range=0.3)
    down_bars = make_bars(
        phase_prices(["up", "calm", "wild", "down"], seed=9), symbol="MSFT", hl_range=0.3
    )

    up = detector.assess("AAPL", up_bars)
    down = detector.assess("MSFT", down_bars)
    assert up is not None and down is not None
    assert up.regime is Regime.TRENDING_UP
    assert down.regime is Regime.TRENDING_DOWN


def test_degenerate_transition_fit_returns_none() -> None:
    rng = np.random.default_rng(0)
    observations = rng.normal(0, 0.01, (100, 4))
    observations[-3:] = np.eye(4)[:3] * 10
    detector = make_detector(training_window=100, seed=0)

    assert detector._fit("AAPL", observations, datetime(2026, 1, 1, tzinfo=UTC)) is None


def test_degenerate_restart_does_not_prevent_valid_assessment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_fit = GaussianHMM.fit
    attempts = 0

    def fit_with_degenerate_first_restart(
        model: GaussianHMM,
        observations: np.ndarray,
    ) -> GaussianHMM:
        nonlocal attempts
        original_fit(model, observations)
        attempts += 1
        if attempts == 1:
            model.transmat_[0] = 0.0
        return model

    monkeypatch.setattr(GaussianHMM, "fit", fit_with_degenerate_first_restart)
    bars = make_bars(phase_prices(["calm", "wild", "down", "up"]), hl_range=0.3)
    assessment = make_detector().assess("AAPL", bars)

    assert attempts == 2
    assert assessment is not None
    assert assessment.regime is Regime.TRENDING_UP
    assert assessment.probabilities is not None
    assert sum(assessment.probabilities.values()) == pytest.approx(1.0)


def test_degenerate_refit_keeps_previous_model(monkeypatch: pytest.MonkeyPatch) -> None:
    detector = make_detector(refit_interval_bars=5)
    bars = make_bars(phase_prices(["up", "down", "calm", "wild"]), hl_range=0.3)
    required = detector.required_lookback()
    assert detector.assess("AAPL", bars[:required]) is not None
    previous = detector._models["AAPL"]
    original_fit = GaussianHMM.fit

    def fit_with_degenerate_transitions(
        model: GaussianHMM,
        observations: np.ndarray,
    ) -> GaussianHMM:
        original_fit(model, observations)
        model.transmat_[0] = 0.0
        return model

    monkeypatch.setattr(GaussianHMM, "fit", fit_with_degenerate_transitions)
    assessment = detector.assess("AAPL", bars[: required + 5])

    assert assessment is not None
    assert assessment.probabilities is not None
    assert sum(assessment.probabilities.values()) == pytest.approx(1.0)
    assert detector._models["AAPL"] is previous


def test_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError, match="n_states"):
        HmmRegimeDetector(n_states=3)
    with pytest.raises(ValueError, match="refit_interval_bars"):
        HmmRegimeDetector(refit_interval_bars=0)
    with pytest.raises(ValueError, match="winsorize_sigma"):
        HmmRegimeDetector(winsorize_sigma=0.0)
