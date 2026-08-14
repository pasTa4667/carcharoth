"""Layered config loader: merge semantics, structure rule, provenance, hash."""

from pathlib import Path

import pytest

from carcharoth.config.loader import (
    OVERRIDES_SOURCE,
    ConfigError,
    load_profile,
    resolve_raw,
)

BASE = """
symbols: []
data: {start: 2025-01-01, end: 2025-03-31}
strategies:
  mean_reversion: {active: true, params: {lookback: 10, entry_z: -1.0}}
  ema_vwap: {active: false, params: {ema_fast: 9}}
regime: null
backtest: {initial_capital: 100000.0, permutation: null}
quicktest: {strategy: mean_reversion, capital: 10000}
optimization:
  study: {name: base-study, n_trials: 2}
  search_space: {}
objectives:
  default: {weights: {sharpe: 1.0}}
risk: {max_open_positions: 5}
"""


def write(config_dir: Path, name: str, text: str) -> None:
    path = config_dir / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    write(tmp_path, "base", BASE)
    write(tmp_path, "symbols/small", "symbols: [AAPL, MSFT]\n")
    write(
        tmp_path,
        "profiles/test",
        """
extends: [base, symbols/small]
strategies:
  mean_reversion: {params: {entry_z: -2.0}}
""",
    )
    return tmp_path


def test_recursive_dict_merge_keeps_sibling_values(config_dir: Path) -> None:
    resolved = load_profile("test", config_dir=config_dir)
    params = resolved.config.strategies["mean_reversion"].params
    assert params["entry_z"] == -2.0  # overridden by the profile
    assert params["lookback"] == 10  # untouched from base


def test_lists_replace_wholesale(config_dir: Path) -> None:
    write(config_dir, "profiles/other", "extends: [base, symbols/small]\nsymbols: [SPY]\n")
    resolved = load_profile("other", config_dir=config_dir)
    assert resolved.config.symbols == ["SPY"]  # never concatenated


def test_extends_order_later_wins(config_dir: Path) -> None:
    write(config_dir, "layers/a", "risk: {max_open_positions: 1}\n")
    write(config_dir, "layers/b", "risk: {max_open_positions: 2}\n")
    write(config_dir, "profiles/ab", "extends: [base, symbols/small, layers/a, layers/b]\n")
    write(config_dir, "profiles/ba", "extends: [base, symbols/small, layers/b, layers/a]\n")
    assert load_profile("ab", config_dir=config_dir).config.risk.max_open_positions == 2
    assert load_profile("ba", config_dir=config_dir).config.risk.max_open_positions == 1


def test_profile_body_wins_over_extends(config_dir: Path) -> None:
    write(
        config_dir,
        "profiles/body",
        "extends: [base, symbols/small]\nrisk: {max_open_positions: 9}\n",
    )
    assert load_profile("body", config_dir=config_dir).config.risk.max_open_positions == 9


def test_shared_layer_via_two_chains_merges_once(config_dir: Path) -> None:
    write(config_dir, "layers/mid", "extends: [base]\nrisk: {max_open_positions: 3}\n")
    write(config_dir, "profiles/diamond", "extends: [base, layers/mid, symbols/small]\n")
    resolved = load_profile("diamond", config_dir=config_dir)
    assert resolved.config.risk.max_open_positions == 3
    assert resolved.layers.count(str(config_dir / "base.yaml")) == 1


def test_cycle_detected(config_dir: Path) -> None:
    write(config_dir, "layers/x", "extends: [layers/y]\n")
    write(config_dir, "layers/y", "extends: [layers/x]\n")
    write(config_dir, "profiles/cyclic", "extends: [base, layers/x]\n")
    with pytest.raises(ConfigError, match="cycle"):
        load_profile("cyclic", config_dir=config_dir)


def test_extends_depth_capped(config_dir: Path) -> None:
    for i in range(10):
        write(config_dir, f"layers/d{i}", f"extends: [layers/d{i + 1}]\n")
    write(config_dir, "layers/d10", "risk: {max_open_positions: 1}\n")
    write(config_dir, "profiles/deep", "extends: [base, layers/d0]\n")
    with pytest.raises(ConfigError, match="depth"):
        load_profile("deep", config_dir=config_dir)


def test_unknown_leaf_path_in_layer_names_the_file(config_dir: Path) -> None:
    write(
        config_dir,
        "profiles/typo",
        "extends: [base, symbols/small]\nrisk: {max_open_positionz: 3}\n",
    )
    with pytest.raises(ConfigError, match=r"typo\.yaml.*risk\.max_open_positionz"):
        load_profile("typo", config_dir=config_dir)


def test_new_top_level_key_rejected(config_dir: Path) -> None:
    write(config_dir, "profiles/newkey", "extends: [base, symbols/small]\nshiny: 1\n")
    with pytest.raises(ConfigError, match="'shiny'"):
        load_profile("newkey", config_dir=config_dir)


def test_null_base_value_is_an_open_slot(config_dir: Path) -> None:
    # base has `backtest.permutation: null` — a layer may fill it freely
    write(
        config_dir,
        "profiles/permute",
        """
extends: [base, symbols/small]
backtest:
  permutation: {method: monte_carlo_trades, n_permutations: 50}
""",
    )
    resolved = load_profile("permute", config_dir=config_dir)
    assert resolved.config.backtest.permutation is not None
    assert resolved.config.backtest.permutation.n_permutations == 50


def test_search_space_replaced_wholesale(config_dir: Path) -> None:
    write(
        config_dir,
        "optimization/two",
        """
optimization:
  search_space:
    strategies.mean_reversion.params.lookback: {type: int, low: 5, high: 50}
    strategies.mean_reversion.params.entry_z: {type: float, low: -3.0, high: -0.5}
""",
    )
    write(
        config_dir,
        "profiles/narrow",
        """
extends: [base, symbols/small, optimization/two]
optimization:
  search_space:
    strategies.mean_reversion.params.entry_z: {type: float, low: -2.0, high: -1.0}
""",
    )
    resolved = load_profile("narrow", config_dir=config_dir)
    assert list(resolved.config.optimization.search_space) == [
        "strategies.mean_reversion.params.entry_z"
    ]


def test_search_space_keys_validated_as_dot_paths(config_dir: Path) -> None:
    write(
        config_dir,
        "profiles/badspace",
        """
extends: [base, symbols/small]
optimization:
  search_space:
    strategies.nope.params.lookback: {type: int, low: 5, high: 50}
""",
    )
    with pytest.raises(ConfigError, match="search_space"):
        load_profile("badspace", config_dir=config_dir)


def test_set_overrides_apply_and_are_checked(config_dir: Path) -> None:
    resolved = load_profile("test", {"risk.max_open_positions": 7}, config_dir=config_dir)
    assert resolved.config.risk.max_open_positions == 7
    with pytest.raises(ConfigError, match=r"risk\.max_open_positionz"):
        load_profile("test", {"risk.max_open_positionz": 7}, config_dir=config_dir)


def test_provenance_tracks_sources(config_dir: Path) -> None:
    resolved = load_profile("test", {"risk.max_open_positions": 7}, config_dir=config_dir)
    prov = resolved.provenance
    assert prov["symbols"] == str(config_dir / "symbols/small.yaml")
    assert prov["strategies.mean_reversion.params.entry_z"] == str(
        config_dir / "profiles/test.yaml"
    )
    assert prov["strategies.mean_reversion.params.lookback"] == str(config_dir / "base.yaml")
    assert prov["risk.max_open_positions"] == OVERRIDES_SOURCE


def test_hash_stable_under_formatting_and_key_order(config_dir: Path, tmp_path: Path) -> None:
    reordered = tmp_path / "reordered"
    write(reordered, "symbols/small", "symbols: [AAPL, MSFT]\n")
    write(reordered, "profiles/test", BASE)  # base content inlined into the profile
    # same values, different file split/order within sections
    write(
        reordered,
        "profiles/whole",
        "extends: [profiles/test, symbols/small]\n"
        "strategies:\n  mean_reversion: {params: {entry_z: -2.0}}\n",
    )
    a = load_profile("test", config_dir=config_dir)
    b = load_profile("whole", config_dir=reordered)
    assert a.hash == b.hash

    c = load_profile("test", {"risk.max_open_positions": 7}, config_dir=config_dir)
    assert c.hash != a.hash


def test_missing_profile_error(config_dir: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_profile("nope", config_dir=config_dir)


def test_resolve_raw_of_bare_base(config_dir: Path) -> None:
    raw, layers, _ = resolve_raw("base", config_dir=config_dir)
    assert raw["symbols"] == []
    assert layers == [str(config_dir / "base.yaml")]
