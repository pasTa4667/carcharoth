"""Dot-path config overrides: strict paths, no input mutation."""

import copy

import pytest

from carcharoth.config.overrides import OverrideError, apply_overrides, validate_override_paths


def raw_config() -> dict:
    return {
        "symbols": ["AAPL"],
        "risk": {"max_open_positions": 5},
        "model": {"decision": {"long_entry_threshold": 0.001}},
    }


def test_sets_nested_value() -> None:
    result = apply_overrides(raw_config(), {"model.decision.long_entry_threshold": -2.5})
    assert result["model"]["decision"]["long_entry_threshold"] == -2.5


def test_sets_multiple_overrides() -> None:
    result = apply_overrides(
        raw_config(),
        {"risk.max_open_positions": 3, "model.decision.long_entry_threshold": -1.0},
    )
    assert result["risk"]["max_open_positions"] == 3
    assert result["model"]["decision"]["long_entry_threshold"] == -1.0


def test_absent_leaf_key_is_created() -> None:
    # schema fields with defaults may be omitted from the YAML entirely
    result = apply_overrides(raw_config(), {"risk.buying_power_buffer": 0.9})
    assert result["risk"]["buying_power_buffer"] == 0.9


def test_missing_intermediate_segment_raises() -> None:
    with pytest.raises(OverrideError, match="features"):
        apply_overrides(raw_config(), {"model.features.0": 10})


def test_non_dict_intermediate_raises() -> None:
    with pytest.raises(OverrideError):
        apply_overrides(raw_config(), {"risk.max_open_positions.limit": 1})


def test_input_is_not_mutated() -> None:
    raw = raw_config()
    snapshot = copy.deepcopy(raw)
    apply_overrides(raw, {"model.decision.long_entry_threshold": -3.0})
    assert raw == snapshot


def test_validate_paths_accepts_valid_and_absent_leaf() -> None:
    validate_override_paths(
        raw_config(),
        ["model.decision.long_entry_threshold", "risk.buying_power_buffer"],
    )


def test_validate_paths_rejects_bad_intermediate() -> None:
    with pytest.raises(OverrideError, match=r"regime\.regimes\.trending"):
        validate_override_paths(raw_config(), ["regime.regimes.trending.params.ema_fast"])
