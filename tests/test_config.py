from pathlib import Path

import pytest
from pydantic import ValidationError

from carcharoth.config import AppConfig, load_config

PROJECT_ROOT = Path(__file__).parent.parent


def test_load_shipped_config() -> None:
    config = load_config(PROJECT_ROOT / "config" / "config.yaml")
    assert config.watchlist.symbols
    assert set(config.strategies) == {"mean_reversion", "ema_vwap"}
    assert config.strategies["mean_reversion"].active
    assert config.regime is not None
    assert config.regime.active is True
    assert config.regime.detector == "hmm"
    assert config.regime.score is not None
    assert config.regime.hmm is not None
    assert set(config.regime.regimes) == {
        "trending_up",
        "range_bound",
        "trending",
        "mean_reverting",
    }
    assert set(config.regime.score.features) == {
        "hurst",
        "vol_clustering",
        "cusum",
        "wasserstein",
    }
    assert config.engine.tick_interval_seconds == 60
    assert 0 < config.risk.max_position_pct_equity <= 1


def test_load_config_roundtrip(tmp_path: Path) -> None:
    yaml_text = """
watchlist:
  symbols: [AAPL]
strategies:
  mean_reversion:
    active: true
    params: {lookback: 30}
"""
    path = tmp_path / "config.yaml"
    path.write_text(yaml_text)
    config = load_config(path)
    assert config.watchlist.symbols == ["AAPL"]
    assert config.strategies["mean_reversion"].params == {"lookback": 30}
    # defaults kick in for omitted sections
    assert config.risk.max_open_positions == 5
    assert config.engine.tick_interval_seconds == 60


def test_empty_watchlist_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"watchlist": {"symbols": []}, "strategies": {"mean_reversion": {"active": True}}}
        )


def test_invalid_risk_params_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "watchlist": {"symbols": ["AAPL"]},
                "strategies": {"mean_reversion": {"active": True}},
                "risk": {"max_position_pct_equity": 1.5},
            }
        )


def make_strategies() -> dict[str, object]:
    return {
        "mean_reversion": {"active": True, "params": {}},
        "ema_vwap": {"active": False, "params": {}},
    }


def make_regime_section(*, active: bool = True, detector: str = "score") -> dict[str, object]:
    return {
        "active": active,
        "detector": detector,
        "score": {"features": {"hurst": {"weight": 1.0}}},
        "hmm": {},
        "regimes": {
            "trending": {"strategy": "ema_vwap"},
            "mean_reverting": {"strategy": "mean_reversion"},
        },
    }


def make_raw_config(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "watchlist": {"symbols": ["AAPL"]},
        "strategies": make_strategies(),
        **overrides,
    }
    return raw


def test_regime_config_defaults() -> None:
    config = AppConfig.model_validate(make_raw_config(regime=make_regime_section()))
    assert config.regime is not None
    assert config.regime.detector == "score"
    assert config.regime.default_regime is None
    assert config.regime.score is not None
    assert config.regime.score.lookback == 400
    assert config.regime.score.evaluate_interval_minutes == 5
    assert config.regime.score.features["hurst"].weight == 1.0
    assert config.regime.evaluate_interval_minutes == 5


def test_cache_config_defaults_to_fully_enabled() -> None:
    config = AppConfig.model_validate(make_raw_config())
    assert config.cache.enabled is True
    assert config.cache.bars is True
    assert config.cache.hmm is True


def test_cache_section_is_parsed() -> None:
    raw = make_raw_config()
    raw["cache"] = {"enabled": True, "bars": False, "hmm": False}
    config = AppConfig.model_validate(raw)
    assert config.cache.bars is False
    assert config.cache.hmm is False


def test_hmm_config_defaults() -> None:
    config = AppConfig.model_validate(make_raw_config(regime=make_regime_section(detector="hmm")))
    assert config.regime is not None
    assert config.regime.hmm is not None
    assert config.regime.hmm.n_states == 4
    assert config.regime.hmm.training_window == 1560
    assert config.regime.hmm.refit_interval_bars == 78
    assert config.regime.hmm.min_confidence == 0.5
    assert config.regime.hmm.covariance_type == "diag"
    assert config.regime.evaluate_interval_minutes == 30


def test_selected_detector_section_must_exist() -> None:
    section = make_regime_section(detector="hmm")
    del section["hmm"]
    with pytest.raises(ValidationError, match="'hmm' section is missing"):
        AppConfig.model_validate(make_raw_config(regime=section))

    section = make_regime_section(detector="score")
    del section["score"]
    with pytest.raises(ValidationError, match="'score' section is missing"):
        AppConfig.model_validate(make_raw_config(regime=section))


def test_unknown_detector_rejected() -> None:
    section = make_regime_section(detector="oracle")
    with pytest.raises(ValidationError):
        AppConfig.model_validate(make_raw_config(regime=section))


def test_hmm_regime_names_accepted() -> None:
    section = make_regime_section(detector="hmm")
    section["regimes"] = {
        "trending_up": {"strategy": "ema_vwap"},
        "range_bound": {"strategy": "mean_reversion"},
    }
    config = AppConfig.model_validate(make_raw_config(regime=section))
    assert config.regime is not None
    assert set(config.regime.regimes) == {"trending_up", "range_bound"}


def test_no_active_strategy_rejected() -> None:
    strategies = {"mean_reversion": {"active": False}, "ema_vwap": {"active": False}}
    with pytest.raises(ValidationError, match="exactly one strategy"):
        AppConfig.model_validate(make_raw_config(strategies=strategies))


def test_multiple_active_strategies_rejected() -> None:
    strategies = {"mean_reversion": {"active": True}, "ema_vwap": {"active": True}}
    with pytest.raises(ValidationError, match="exactly one strategy"):
        AppConfig.model_validate(make_raw_config(strategies=strategies))


def test_active_regime_referencing_unknown_strategy_rejected() -> None:
    section = make_regime_section()
    regimes = section["regimes"]
    assert isinstance(regimes, dict)
    regimes["trending"] = {"strategy": "does_not_exist"}
    with pytest.raises(ValidationError, match="not defined in 'strategies'"):
        AppConfig.model_validate(make_raw_config(regime=section))


def test_unknown_regime_key_rejected() -> None:
    section = make_regime_section()
    regimes = section["regimes"]
    assert isinstance(regimes, dict)
    regimes["sideways"] = {"strategy": "mean_reversion"}
    with pytest.raises(ValidationError, match="unknown regimes"):
        AppConfig.model_validate(make_raw_config(regime=section))


def test_partial_regime_mapping_allowed() -> None:
    section = make_regime_section()
    regimes = section["regimes"]
    assert isinstance(regimes, dict)
    del regimes["trending"]
    config = AppConfig.model_validate(make_raw_config(regime=section))
    assert config.regime is not None
    assert set(config.regime.regimes) == {"mean_reverting"}


def test_unknown_default_regime_rejected() -> None:
    section = make_regime_section()
    section["default_regime"] = "sideways"
    with pytest.raises(ValidationError, match="default_regime"):
        AppConfig.model_validate(make_raw_config(regime=section))


def test_regime_requires_at_least_one_feature() -> None:
    section = make_regime_section()
    section["score"] = {"features": {}}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(make_raw_config(regime=section))
