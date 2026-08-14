"""Validation of the root RunConfig schema and its views."""

import pytest
from pydantic import ValidationError

from carcharoth.config import RunConfig, run_config_from_stored


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
        "symbols": ["AAPL"],
        "data": {"start": "2025-01-01", "end": "2025-06-30"},
        "strategies": make_strategies(),
        **overrides,
    }
    return raw


def test_defaults_kick_in_for_omitted_sections() -> None:
    config = RunConfig.model_validate(make_raw_config())
    assert config.symbols == ["AAPL"]
    assert config.risk.max_open_positions == 5
    assert config.engine.tick_interval_seconds == 60
    assert config.quicktest.capital == 10_000.0
    assert config.optimization.study is None
    assert config.optimization.search_space == {}


def test_empty_symbols_rejected() -> None:
    with pytest.raises(ValidationError):
        RunConfig.model_validate(make_raw_config(symbols=[]))


def test_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RunConfig.model_validate(make_raw_config(watchlist={"symbols": ["AAPL"]}))
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RunConfig.model_validate(make_raw_config(risk={"max_open_positionz": 3}))


def test_data_end_before_start_rejected() -> None:
    with pytest.raises(ValidationError, match=r"data\.end must not be before"):
        RunConfig.model_validate(make_raw_config(data={"start": "2025-06-30", "end": "2025-01-01"}))


def test_invalid_risk_params_rejected() -> None:
    with pytest.raises(ValidationError):
        RunConfig.model_validate(make_raw_config(risk={"max_position_pct_equity": 1.5}))


def test_regime_config_defaults() -> None:
    config = RunConfig.model_validate(make_raw_config(regime=make_regime_section()))
    assert config.regime is not None
    assert config.regime.detector == "score"
    assert config.regime.default_regime is None
    assert config.regime.score is not None
    assert config.regime.score.lookback == 400
    assert config.regime.score.evaluate_interval_minutes == 5
    assert config.regime.score.features["hurst"].weight == 1.0
    assert config.regime.evaluate_interval_minutes == 5


def test_cache_config_defaults_to_fully_enabled() -> None:
    config = RunConfig.model_validate(make_raw_config())
    assert config.cache.enabled is True
    assert config.cache.bars is True
    assert config.cache.hmm is True


def test_hmm_config_defaults() -> None:
    config = RunConfig.model_validate(make_raw_config(regime=make_regime_section(detector="hmm")))
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
        RunConfig.model_validate(make_raw_config(regime=section))

    section = make_regime_section(detector="score")
    del section["score"]
    with pytest.raises(ValidationError, match="'score' section is missing"):
        RunConfig.model_validate(make_raw_config(regime=section))


def test_unknown_detector_rejected() -> None:
    section = make_regime_section(detector="oracle")
    with pytest.raises(ValidationError):
        RunConfig.model_validate(make_raw_config(regime=section))


def test_hmm_regime_names_accepted() -> None:
    section = make_regime_section(detector="hmm")
    section["regimes"] = {
        "trending_up": {"strategy": "ema_vwap"},
        "range_bound": {"strategy": "mean_reversion"},
    }
    config = RunConfig.model_validate(make_raw_config(regime=section))
    assert config.regime is not None
    assert set(config.regime.regimes) == {"trending_up", "range_bound"}


def test_no_active_strategy_rejected() -> None:
    strategies = {"mean_reversion": {"active": False}, "ema_vwap": {"active": False}}
    with pytest.raises(ValidationError, match="exactly one strategy"):
        RunConfig.model_validate(make_raw_config(strategies=strategies))


def test_multiple_active_strategies_rejected() -> None:
    strategies = {"mean_reversion": {"active": True}, "ema_vwap": {"active": True}}
    with pytest.raises(ValidationError, match="exactly one strategy"):
        RunConfig.model_validate(make_raw_config(strategies=strategies))


def test_active_regime_referencing_unknown_strategy_rejected() -> None:
    section = make_regime_section()
    regimes = section["regimes"]
    assert isinstance(regimes, dict)
    regimes["trending"] = {"strategy": "does_not_exist"}
    with pytest.raises(ValidationError, match="not defined in 'strategies'"):
        RunConfig.model_validate(make_raw_config(regime=section))


def test_unknown_regime_key_rejected() -> None:
    section = make_regime_section()
    regimes = section["regimes"]
    assert isinstance(regimes, dict)
    regimes["sideways"] = {"strategy": "mean_reversion"}
    with pytest.raises(ValidationError, match="unknown regimes"):
        RunConfig.model_validate(make_raw_config(regime=section))


def test_quicktest_unknown_strategy_reference_rejected() -> None:
    with pytest.raises(ValidationError, match=r"quicktest\.strategy"):
        RunConfig.model_validate(make_raw_config(quicktest={"strategy": "nope"}))


def test_quicktest_view_resolves_params_from_shared_strategies() -> None:
    raw = make_raw_config(
        strategies={
            "mean_reversion": {"active": True, "params": {"lookback": 30, "entry_z": -1.5}},
        },
        quicktest={"strategy": "mean_reversion", "capital": 5000},
    )
    view = RunConfig.model_validate(raw).quicktest_view()
    assert view.strategy.name == "mean_reversion"
    assert view.strategy.params == {"lookback": 30, "entry_z": -1.5}
    assert view.capital == 5000
    assert view.symbols == ["AAPL"]
    assert str(view.start) == "2025-01-01"


def test_quicktest_view_falls_back_to_single_active_strategy() -> None:
    config = RunConfig.model_validate(make_raw_config())
    assert config.quicktest.strategy is None
    assert config.quicktest_view().strategy.name == "mean_reversion"


def test_optimize_view_requires_study_and_search_space() -> None:
    config = RunConfig.model_validate(make_raw_config())
    with pytest.raises(ValueError, match=r"optimization\.study"):
        config.optimize_view()

    raw = make_raw_config(
        optimization={"study": {"name": "s", "n_trials": 2}},
    )
    with pytest.raises(ValueError, match="search_space is empty"):
        RunConfig.model_validate(raw).optimize_view()


def test_optimize_view_takes_window_and_symbols_from_shared_sections() -> None:
    raw = make_raw_config(
        optimization={
            "study": {"name": "s", "n_trials": 2},
            "search_space": {"risk.max_open_positions": {"type": "int", "low": 1, "high": 10}},
        },
    )
    view = RunConfig.model_validate(raw).optimize_view()
    assert view.study.name == "s"
    assert view.backtest.symbols == ["AAPL"]
    assert str(view.backtest.start) == "2025-01-01"
    assert str(view.backtest.end) == "2025-06-30"


def test_stored_legacy_appconfig_shape_is_translated() -> None:
    legacy = {
        "watchlist": {"symbols": ["AAPL", "MSFT"]},
        "strategies": make_strategies(),
        "risk": {"max_open_positions": 3},
    }
    config = run_config_from_stored(legacy)
    assert config.symbols == ["AAPL", "MSFT"]
    assert config.risk.max_open_positions == 3


def test_stored_current_shape_passes_through() -> None:
    config = run_config_from_stored(make_raw_config())
    assert config.symbols == ["AAPL"]
