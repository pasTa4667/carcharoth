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
    assert config.regime.active is False
    assert set(config.regime.regimes) == {"trending", "mean_reverting"}
    assert set(config.regime.features) == {"hurst", "vol_clustering", "cusum", "wasserstein"}
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


def make_regime_section(*, active: bool = True) -> dict[str, object]:
    return {
        "active": active,
        "features": {"hurst": {"weight": 1.0}},
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
    assert config.regime.lookback == 400
    assert config.regime.evaluate_every_ticks == 5
    assert config.regime.default_regime == "mean_reverting"
    assert config.regime.features["hurst"].weight == 1.0


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


def test_unmapped_regime_rejected() -> None:
    section = make_regime_section()
    regimes = section["regimes"]
    assert isinstance(regimes, dict)
    del regimes["trending"]
    with pytest.raises(ValidationError, match="without a mapped strategy"):
        AppConfig.model_validate(make_raw_config(regime=section))


def test_unknown_default_regime_rejected() -> None:
    section = make_regime_section()
    section["default_regime"] = "sideways"
    with pytest.raises(ValidationError, match="default_regime"):
        AppConfig.model_validate(make_raw_config(regime=section))


def test_regime_requires_at_least_one_feature() -> None:
    section = make_regime_section()
    section["features"] = {}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(make_raw_config(regime=section))
