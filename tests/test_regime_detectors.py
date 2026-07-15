"""Detector builder: config -> concrete detector."""

import logging

import pytest

from carcharoth.config.app_config import RegimeConfig
from carcharoth.regime.detectors import build_detector
from carcharoth.regime.hmm import HmmRegimeDetector
from carcharoth.regime.score_detector import ScoreRegimeDetector
from tests.fakes import InMemoryByteStore


def make_regime_config(detector: str, regimes: dict[str, object] | None = None) -> RegimeConfig:
    return RegimeConfig.model_validate(
        {
            "active": True,
            "detector": detector,
            "score": {
                "lookback": 300,
                "features": {"hurst": {"weight": 2.0, "params": {"min_window": 8}}},
            },
            "hmm": {"training_window": 500},
            "regimes": regimes
            or {
                "trending": {"strategy": "ema_vwap"},
                "trending_up": {"strategy": "ema_vwap"},
            },
        }
    )


def test_builds_score_detector_from_its_section() -> None:
    detector = build_detector(make_regime_config("score"))
    assert isinstance(detector, ScoreRegimeDetector)
    assert detector.required_lookback() == 300


def test_builds_hmm_detector_from_its_section() -> None:
    detector = build_detector(make_regime_config("hmm"))
    assert isinstance(detector, HmmRegimeDetector)
    # training_window + indicator warm-up padding
    assert detector.required_lookback() > 500
    assert detector._fit_cache is None  # no store -> uncached


def test_hmm_fit_store_wires_a_fit_cache() -> None:
    detector = build_detector(make_regime_config("hmm"), hmm_fit_store=InMemoryByteStore())
    assert isinstance(detector, HmmRegimeDetector)
    assert detector._fit_cache is not None


def test_score_detector_ignores_the_fit_store() -> None:
    detector = build_detector(make_regime_config("score"), hmm_fit_store=InMemoryByteStore())
    assert isinstance(detector, ScoreRegimeDetector)


def test_warns_when_no_mapped_regime_is_emittable(caplog: pytest.LogCaptureFixture) -> None:
    config = make_regime_config("score", regimes={"trending_up": {"strategy": "ema_vwap"}})
    with caplog.at_level(logging.WARNING):
        build_detector(config)
    assert "nothing will ever trade" in caplog.text


def test_no_warning_when_a_mapped_regime_is_emittable(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        build_detector(make_regime_config("hmm"))
    assert "nothing will ever trade" not in caplog.text
