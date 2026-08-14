"""Validation of the optimization study config schema."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from carcharoth.config.optimize_config import (
    BacktestWindowConfig,
    CategoricalParam,
    ConstraintConfig,
    FloatParam,
    IntParam,
    OptimizeConfig,
)

MINIMAL = {
    "study": {"name": "test", "n_trials": 5},
    "backtest": {"start": "2026-06-01", "end": "2026-06-30"},
    "search_space": {"risk.max_open_positions": {"type": "int", "low": 1, "high": 10}},
}


def test_minimal_config_valid() -> None:
    config = OptimizeConfig.model_validate(MINIMAL)
    assert config.objective == "default"
    assert config.constraints == []
    assert config.study.sampler_seed is None
    assert config.backtest.symbols is None


def test_load_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "optimize.yaml"
    path.write_text(
        """
study: {name: sweep, n_trials: 3, sampler_seed: 7}
backtest: {start: 2026-06-01, end: 2026-06-02, symbols: [AAPL]}
objective: growth
constraints:
  - {metric: num_trades, min: 20}
search_space:
  strategies.mean_reversion.params.entry_z: {type: float, low: -3.0, high: -0.5}
  strategies.mean_reversion.params.timeframe_minutes: {type: categorical, choices: [5, 15]}
"""
    )
    config = OptimizeConfig.model_validate(yaml.safe_load(path.read_text()))
    assert config.study.name == "sweep"
    assert config.objective == "growth"
    assert config.constraints[0].metric == "num_trades"
    entry_z = config.search_space["strategies.mean_reversion.params.entry_z"]
    assert isinstance(entry_z, FloatParam)
    timeframe = config.search_space["strategies.mean_reversion.params.timeframe_minutes"]
    assert isinstance(timeframe, CategoricalParam)


def test_window_datetime_properties() -> None:
    window = BacktestWindowConfig(start=date(2026, 6, 1), end=date(2026, 6, 30))
    assert window.start_dt == datetime(2026, 6, 1, tzinfo=UTC)
    assert window.end_exclusive_dt == datetime(2026, 7, 1, tzinfo=UTC)


def test_window_end_before_start_rejected() -> None:
    with pytest.raises(ValidationError):
        BacktestWindowConfig(start=date(2026, 6, 30), end=date(2026, 6, 1))


def test_empty_search_space_rejected() -> None:
    with pytest.raises(ValidationError):
        OptimizeConfig.model_validate({**MINIMAL, "search_space": {}})


def test_int_low_must_be_below_high() -> None:
    with pytest.raises(ValidationError):
        IntParam(type="int", low=10, high=10)


def test_float_step_and_log_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        FloatParam(type="float", low=0.1, high=1.0, step=0.1, log=True)


def test_categorical_needs_two_choices() -> None:
    with pytest.raises(ValidationError):
        CategoricalParam(type="categorical", choices=[5])


def test_constraint_needs_a_bound() -> None:
    with pytest.raises(ValidationError):
        ConstraintConfig(metric="num_trades")


def test_workers_defaults_to_one() -> None:
    assert OptimizeConfig.model_validate(MINIMAL).study.workers == 1


def test_zero_workers_rejected() -> None:
    invalid = {**MINIMAL, "study": {**MINIMAL["study"], "workers": 0}}
    with pytest.raises(ValidationError):
        OptimizeConfig.model_validate(invalid)
