from pathlib import Path

import pytest
from pydantic import ValidationError

from carcharoth.config import AppConfig, load_config

PROJECT_ROOT = Path(__file__).parent.parent


def test_load_shipped_config() -> None:
    config = load_config(PROJECT_ROOT / "config" / "config.yaml")
    assert config.watchlist.symbols
    assert config.strategy.name == "mean_reversion"
    assert config.engine.tick_interval_seconds == 60
    assert 0 < config.risk.max_position_pct_equity <= 1


def test_load_config_roundtrip(tmp_path: Path) -> None:
    yaml_text = """
watchlist:
  symbols: [AAPL]
strategy:
  name: mean_reversion
  params: {lookback: 30}
"""
    path = tmp_path / "config.yaml"
    path.write_text(yaml_text)
    config = load_config(path)
    assert config.watchlist.symbols == ["AAPL"]
    assert config.strategy.params == {"lookback": 30}
    # defaults kick in for omitted sections
    assert config.risk.max_open_positions == 5
    assert config.engine.tick_interval_seconds == 60


def test_empty_watchlist_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"watchlist": {"symbols": []}, "strategy": {"name": "mean_reversion"}}
        )


def test_invalid_risk_params_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "watchlist": {"symbols": ["AAPL"]},
                "strategy": {"name": "mean_reversion"},
                "risk": {"max_position_pct_equity": 1.5},
            }
        )
