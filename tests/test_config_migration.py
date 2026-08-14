"""The refactor changes no behaviour: shipped profiles resolve to the same
effective values as the pre-refactor single-file configs (pinned as fixtures
in tests/fixtures/legacy/), modulo the intentional JPM drift fix.

Also validates every shipped profile so a broken layer file fails CI.
"""

from pathlib import Path

import pytest
import yaml

from carcharoth.config.app_config import (
    BacktestConfig,
    CacheConfig,
    EngineConfig,
    ObjectiveConfig,
    RegimeConfig,
    RiskConfig,
    StrategyConfig,
)
from carcharoth.config.loader import load_profile
from carcharoth.config.optimize_config import OptimizeConfig
from carcharoth.config.quicktest_config import QuickTestConfig

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
LEGACY = Path(__file__).parent / "fixtures" / "legacy"

SHIPPED_PROFILES = ["quicktest", "backtest", "optimization", "smoke", "trading/paper"]


@pytest.mark.parametrize("profile", SHIPPED_PROFILES)
def test_every_shipped_profile_is_valid(profile: str) -> None:
    resolved = load_profile(profile, config_dir=CONFIG_DIR)
    assert resolved.config.symbols
    assert resolved.hash


def legacy(name: str) -> dict:
    return yaml.safe_load((LEGACY / name).read_text())


def test_backtest_profile_matches_legacy_config_yaml() -> None:
    old = legacy("config.yaml")
    new = load_profile("backtest", config_dir=CONFIG_DIR).config

    assert new.symbols == old["watchlist"]["symbols"]
    assert new.engine == EngineConfig.model_validate(old["engine"])
    assert new.strategies == {
        name: StrategyConfig.model_validate(cfg) for name, cfg in old["strategies"].items()
    }
    assert new.regime == RegimeConfig.model_validate(old["regime"])
    assert new.backtest == BacktestConfig.model_validate(old["backtest"])
    assert new.cache == CacheConfig.model_validate(old["cache"])
    assert new.risk == RiskConfig.model_validate(old["risk"])
    assert new.objectives == {
        name: ObjectiveConfig.model_validate(cfg) for name, cfg in old["objectives"].items()
    }


def test_paper_profile_matches_legacy_config_yaml() -> None:
    backtest = load_profile("backtest", config_dir=CONFIG_DIR)
    paper = load_profile("trading/paper", config_dir=CONFIG_DIR)
    # paper trading currently runs the exact backtest config
    assert paper.hash == backtest.hash


def test_quicktest_profile_matches_legacy_quicktest_yaml() -> None:
    old = QuickTestConfig.model_validate(legacy("quicktest.yaml"))
    new = load_profile("quicktest", config_dir=CONFIG_DIR).config.quicktest_view()
    assert new == old


def test_optimization_profile_matches_legacy_optimize_yaml() -> None:
    old_raw = legacy("optimize.yaml")
    new = load_profile("optimization", config_dir=CONFIG_DIR).config.optimize_view()
    old = OptimizeConfig.model_validate(old_raw)

    assert new.study == old.study
    assert new.objective == old.objective
    assert new.constraints == old.constraints
    assert new.search_space == old.search_space
    assert new.backtest.start == old.backtest.start
    assert new.backtest.end == old.backtest.end
    # Intentional fix: optimize.yaml had drifted (JPM missing); the shared
    # symbol layer restores it.
    assert old.backtest.symbols is not None
    assert set(new.backtest.symbols or []) - set(old.backtest.symbols) == {"JPM"}


def test_quicktest_params_identical_to_backtest_params() -> None:
    """The duplication the refactor kills: quicktest and backtest are
    guaranteed to test the same strategy params."""
    resolved = load_profile("quicktest", config_dir=CONFIG_DIR).config
    view = resolved.quicktest_view()
    assert view.strategy.params == resolved.strategies[view.strategy.name].params
