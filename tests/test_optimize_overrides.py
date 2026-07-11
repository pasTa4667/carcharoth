"""Dot-path config overrides: strict paths, no input mutation."""

import copy

import pytest

from carcharoth.optimize.overrides import OverrideError, apply_overrides, validate_override_paths


def raw_config() -> dict:
    return {
        "watchlist": {"symbols": ["AAPL"]},
        "risk": {"max_open_positions": 5},
        "regime": {
            "regimes": {
                "mean_reverting": {"strategy": "mean_reversion", "params": {"entry_z": -1.5}}
            }
        },
    }


def test_sets_nested_value() -> None:
    result = apply_overrides(raw_config(), {"regime.regimes.mean_reverting.params.entry_z": -2.5})
    assert result["regime"]["regimes"]["mean_reverting"]["params"]["entry_z"] == -2.5


def test_sets_multiple_overrides() -> None:
    result = apply_overrides(
        raw_config(),
        {"risk.max_open_positions": 3, "regime.regimes.mean_reverting.params.entry_z": -1.0},
    )
    assert result["risk"]["max_open_positions"] == 3
    assert result["regime"]["regimes"]["mean_reverting"]["params"]["entry_z"] == -1.0


def test_absent_leaf_key_is_created() -> None:
    # schema fields with defaults may be omitted from the YAML entirely
    result = apply_overrides(raw_config(), {"risk.buying_power_buffer": 0.9})
    assert result["risk"]["buying_power_buffer"] == 0.9


def test_missing_intermediate_segment_raises() -> None:
    with pytest.raises(OverrideError, match="strategy"):
        apply_overrides(raw_config(), {"strategy.params.lookback": 10})


def test_non_dict_intermediate_raises() -> None:
    with pytest.raises(OverrideError):
        apply_overrides(raw_config(), {"risk.max_open_positions.limit": 1})


def test_input_is_not_mutated() -> None:
    raw = raw_config()
    snapshot = copy.deepcopy(raw)
    apply_overrides(raw, {"regime.regimes.mean_reverting.params.entry_z": -3.0})
    assert raw == snapshot


def test_validate_paths_accepts_valid_and_absent_leaf() -> None:
    validate_override_paths(
        raw_config(),
        ["regime.regimes.mean_reverting.params.entry_z", "risk.buying_power_buffer"],
    )


def test_validate_paths_rejects_bad_intermediate() -> None:
    with pytest.raises(OverrideError, match=r"regime\.regimes\.trending"):
        validate_override_paths(raw_config(), ["regime.regimes.trending.params.ema_fast"])
