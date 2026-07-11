"""Search-space entries map onto Optuna's suggest API, keyed by dot-path."""

import optuna
import pytest

from carcharoth.config.optimize_config import CategoricalParam, FloatParam, IntParam
from carcharoth.services.optuna.search_space import suggest_overrides


def test_int_param() -> None:
    trial = optuna.trial.FixedTrial({"risk.max_open_positions": 4})
    overrides = suggest_overrides(
        trial, {"risk.max_open_positions": IntParam(type="int", low=1, high=10)}
    )
    assert overrides == {"risk.max_open_positions": 4}


def test_float_param_with_step() -> None:
    trial = optuna.trial.FixedTrial({"strategy.params.entry_z": -1.5})
    overrides = suggest_overrides(
        trial,
        {"strategy.params.entry_z": FloatParam(type="float", low=-3.0, high=-0.5, step=0.1)},
    )
    assert overrides == {"strategy.params.entry_z": pytest.approx(-1.5)}


def test_float_param_log() -> None:
    trial = optuna.trial.FixedTrial({"risk.max_position_pct_equity": 0.1})
    overrides = suggest_overrides(
        trial,
        {"risk.max_position_pct_equity": FloatParam(type="float", low=0.05, high=0.25, log=True)},
    )
    assert overrides == {"risk.max_position_pct_equity": pytest.approx(0.1)}


def test_categorical_param() -> None:
    trial = optuna.trial.FixedTrial({"strategy.params.timeframe_minutes": 15})
    overrides = suggest_overrides(
        trial,
        {
            "strategy.params.timeframe_minutes": CategoricalParam(
                type="categorical", choices=[5, 15]
            )
        },
    )
    assert overrides == {"strategy.params.timeframe_minutes": 15}


def test_multiple_params_all_suggested() -> None:
    trial = optuna.trial.FixedTrial(
        {"strategy.params.lookback": 20, "strategy.params.entry_z": -2.0}
    )
    overrides = suggest_overrides(
        trial,
        {
            "strategy.params.lookback": IntParam(type="int", low=10, high=60),
            "strategy.params.entry_z": FloatParam(type="float", low=-3.0, high=-0.5),
        },
    )
    assert set(overrides) == {"strategy.params.lookback", "strategy.params.entry_z"}
