from pathlib import Path

import pytest
from pydantic import ValidationError

from carcharoth.config.app_config import ModelConfig, RegimeConfig
from carcharoth.config.loader import load_profile
from carcharoth.config.run_config import RunConfig, run_config_from_stored


@pytest.mark.parametrize(
    "profile", ["training", "quicktest", "backtest", "optimization", "smoke", "trading/paper"]
)
def test_shipped_profiles_validate(profile: str) -> None:
    resolved = load_profile(profile)
    assert resolved.config.symbols
    assert resolved.config.model.target.horizon_bars == 5


def test_model_defaults_to_regression_and_spy() -> None:
    model = load_profile("training").config.model
    assert model.target.type == "regression"
    assert model.benchmark_symbol == "SPY"
    assert len(model.features) == 18


def test_classification_target_is_swappable() -> None:
    raw = load_profile("training").config.model.model_dump(mode="python")
    raw["target"] = {
        "type": "classification",
        "horizon_bars": 10,
        "neutral_band": 0.001,
    }
    model = ModelConfig.model_validate(raw)
    assert model.target.type == "classification"
    assert model.target.horizon_bars == 10


def test_flatten_window_must_cover_next_bar() -> None:
    raw = load_profile("training").config.model.model_dump(mode="python")
    raw["decision"]["flatten_minutes"] = 1
    with pytest.raises(ValidationError, match="flatten_minutes"):
        ModelConfig.model_validate(raw)


def test_quicktest_view_reuses_exact_model_config() -> None:
    config = load_profile("quicktest").config
    assert config.quicktest_view().model == config.model


def test_fixed_artifact_optimizer_rejects_retraining_paths() -> None:
    config = load_profile("optimization").config
    raw = config.model_dump(mode="python")
    raw["model"]["artifact_path"] = "artifacts/models/example"
    raw["optimization"]["search_space"] = {
        "model.target.horizon_bars": {"type": "int", "low": 2, "high": 10}
    }
    changed = RunConfig.model_validate(raw)
    with pytest.raises(ValueError, match="retraining-required"):
        changed.optimize_view()


def test_legacy_stored_strategy_config_is_not_replayable() -> None:
    with pytest.raises(ValidationError):
        run_config_from_stored({"symbols": ["AAPL"], "strategies": {}})


def test_regime_detector_config_remains_standalone() -> None:
    config = RegimeConfig.model_validate(
        {
            "detector": "score",
            "score": {"features": {"hurst": {"weight": 1.0, "params": {}}}},
            "hmm": {"training_window": 500},
        }
    )
    assert config.detector == "score"


def test_artifact_path_is_explicit_and_optional_for_training() -> None:
    assert load_profile("training").config.model.artifact_path is None
    model = load_profile(
        "training", {"model.artifact_path": Path("artifacts/models/example")}
    ).config.model
    assert model.artifact_path == Path("artifacts/models/example")
