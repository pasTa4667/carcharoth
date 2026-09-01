"""Disconnected regime detectors remain buildable for future model context."""

from carcharoth.config.app_config import RegimeConfig
from carcharoth.regime.detectors import build_detector
from carcharoth.regime.hmm import HmmRegimeDetector
from carcharoth.regime.score_detector import ScoreRegimeDetector
from tests.fakes import InMemoryByteStore


def config(name: str) -> RegimeConfig:
    return RegimeConfig.model_validate(
        {
            "detector": name,
            "score": {
                "lookback": 300,
                "features": {"hurst": {"weight": 2.0, "params": {"min_window": 8}}},
            },
            "hmm": {"training_window": 500},
        }
    )


def test_score_and_hmm_build_without_trading_assignments() -> None:
    assert isinstance(build_detector(config("score")), ScoreRegimeDetector)
    assert isinstance(build_detector(config("hmm")), HmmRegimeDetector)


def test_hmm_cache_remains_available_but_is_not_runtime_wired() -> None:
    detector = build_detector(config("hmm"), hmm_fit_store=InMemoryByteStore())
    assert isinstance(detector, HmmRegimeDetector)
    assert detector._fit_cache is not None
