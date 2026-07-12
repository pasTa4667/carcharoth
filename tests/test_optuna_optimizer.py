"""OptunaOptimizer end-to-end against in-memory studies and a fake backtest."""

import optuna
import pytest

from carcharoth.config.app_config import ObjectiveConfig
from carcharoth.config.optimize_config import OptimizeConfig
from carcharoth.domain.models import MetricValue
from carcharoth.services.optuna.optimizer import OptunaOptimizer
from tests.fakes import FakeBacktestFunc

RAW_CONFIG = {
    "watchlist": {"symbols": ["AAPL"]},
    "strategies": {"mean_reversion": {"active": True, "params": {"lookback": 20}}},
    "objectives": {"default": {"weights": {"sharpe": 1.0}}},
}

OBJECTIVE = ObjectiveConfig(weights={"sharpe": 1.0})

optuna.logging.set_verbosity(optuna.logging.WARNING)


def optimize_config(n_trials: int = 3, **kwargs: object) -> OptimizeConfig:
    return OptimizeConfig.model_validate(
        {
            "study": {"name": "test-study", "n_trials": n_trials, "sampler_seed": 1},
            "backtest": {"start": "2026-06-01", "end": "2026-06-30"},
            "search_space": {
                "strategies.mean_reversion.params.lookback": {"type": "int", "low": 10, "high": 60}
            },
            **kwargs,
        }
    )


def run_metrics(fitness: float, **extra: float) -> list[MetricValue]:
    metrics = [MetricValue("fitness_default", fitness)]
    metrics.extend(MetricValue(name, value) for name, value in extra.items())
    return metrics


def make_optimizer(fake: FakeBacktestFunc, config: OptimizeConfig | None = None) -> OptunaOptimizer:
    return OptunaOptimizer(
        run_backtest=fake,
        raw_config=RAW_CONFIG,
        optimize_config=config or optimize_config(),
        objective=OBJECTIVE,
        symbols=["AAPL"],
    )


def test_best_trial_matches_scripted_fitness() -> None:
    fake = FakeBacktestFunc([run_metrics(1.0), run_metrics(3.0), run_metrics(2.0)])
    result = make_optimizer(fake).optimize()

    assert result.n_complete == 3
    assert result.n_failed == 0
    assert result.best_score == pytest.approx(3.0)
    assert result.best_trial_number == 1
    assert result.best_run_id == fake.run_ids[1]
    assert set(result.best_params) == {"strategies.mean_reversion.params.lookback"}


def test_every_trial_links_its_run_id() -> None:
    fake = FakeBacktestFunc([run_metrics(1.0)])
    optimizer = make_optimizer(fake)
    optimizer.optimize()

    assert optimizer.last_study is not None
    linked = [trial.user_attrs["run_id"] for trial in optimizer.last_study.trials]
    assert linked == [str(run_id) for run_id in fake.run_ids]


def test_suggested_value_lands_in_received_config() -> None:
    fake = FakeBacktestFunc([run_metrics(1.0)])
    optimizer = make_optimizer(fake)
    optimizer.optimize()

    assert optimizer.last_study is not None
    path = "strategies.mean_reversion.params.lookback"
    for config, trial in zip(fake.calls, optimizer.last_study.trials, strict=True):
        assert config.strategies["mean_reversion"].params["lookback"] == trial.params[path]


def test_backtest_error_fails_trial_but_study_continues() -> None:
    fake = FakeBacktestFunc([run_metrics(1.0), ValueError("bad param combo"), run_metrics(2.0)])
    result = make_optimizer(fake).optimize()

    assert result.n_complete == 2
    assert result.n_failed == 1
    assert result.best_score == pytest.approx(2.0)


def test_constraint_violation_penalizes_and_marks_infeasible() -> None:
    config = optimize_config(n_trials=2, constraints=[{"metric": "num_trades", "min": 20}])
    fake = FakeBacktestFunc([run_metrics(5.0, num_trades=10.0), run_metrics(1.0, num_trades=30.0)])
    optimizer = make_optimizer(fake, config)
    result = optimizer.optimize()

    assert result.n_infeasible == 1
    # despite its higher raw fitness the infeasible trial must not win
    assert result.best_score == pytest.approx(1.0)
    assert optimizer.last_study is not None
    infeasible = optimizer.last_study.trials[0]
    assert infeasible.user_attrs["infeasible"] is True
    assert infeasible.user_attrs["violated_constraints"] == ["num_trades=10 < min 20"]
    assert infeasible.value == OBJECTIVE.penalty_score
    # the run itself stays linked and untouched
    assert infeasible.user_attrs["run_id"] == str(fake.run_ids[0])


def test_missing_fitness_metric_fails_trial() -> None:
    fake = FakeBacktestFunc([[MetricValue("sharpe", 1.0)]])
    result = make_optimizer(fake).optimize()

    assert result.n_complete == 0
    assert result.n_failed == 3
    assert result.best_trial_number is None
    assert result.best_score is None
    assert result.best_run_id is None


def test_invalid_search_space_path_fails_fast() -> None:
    bad_path = "regime.regimes.trending.params.ema_fast"
    config = optimize_config(search_space={bad_path: {"type": "int", "low": 5, "high": 20}})
    fake = FakeBacktestFunc([run_metrics(1.0)])
    with pytest.raises(ValueError, match="invalid override path"):
        make_optimizer(fake, config).optimize()
    assert fake.calls == []  # no trial ever ran


def test_sampler_seed_override_beats_config_seed() -> None:
    def suggested_lookbacks(config_seed: int) -> list[float]:
        fake = FakeBacktestFunc([run_metrics(1.0)])
        config = optimize_config(
            n_trials=5,
            study={"name": "test-study", "n_trials": 5, "sampler_seed": config_seed},
        )
        OptunaOptimizer(
            run_backtest=fake,
            raw_config=RAW_CONFIG,
            optimize_config=config,
            objective=OBJECTIVE,
            symbols=["AAPL"],
            sampler_seed=42,
        ).optimize()
        assert config.study.sampler_seed == config_seed
        return [call.strategies["mean_reversion"].params["lookback"] for call in fake.calls]

    # Different config seeds, same override: identical suggestions prove the
    # override wins (parallel workers rely on this for per-worker seeds).
    assert suggested_lookbacks(1) == suggested_lookbacks(2)
