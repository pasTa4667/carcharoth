"""Optuna-backed ParameterOptimizer.

Each trial suggests parameter overrides, runs one normal fully-persisted
backtest through the injected ``BacktestFunc``, and reads back the
``fitness_<objective>`` metric that the run's analysis already computed.
The trial -> run linkage lives only in Optuna's trial user attributes; the
backtest side knows nothing about the optimizer.
"""

import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import optuna
from pydantic import ValidationError

from carcharoth.analysis.objective import fitness_metric_name
from carcharoth.config.app_config import AppConfig, ObjectiveConfig
from carcharoth.config.optimize_config import OptimizeConfig
from carcharoth.domain.models import OptimizationResult
from carcharoth.interfaces.optimization import BacktestFunc, ParameterOptimizer
from carcharoth.optimize.constraints import violated_constraints
from carcharoth.optimize.overrides import apply_overrides, validate_override_paths
from carcharoth.services.optuna.search_space import suggest_overrides

logger = logging.getLogger(__name__)


class OptunaOptimizer(ParameterOptimizer):
    def __init__(
        self,
        run_backtest: BacktestFunc,
        raw_config: dict[str, Any],
        optimize_config: OptimizeConfig,
        objective: ObjectiveConfig,
        symbols: Sequence[str],
        storage: str | optuna.storages.BaseStorage | None = None,
        n_trials: int | None = None,
        study_name: str | None = None,
        sampler_seed: int | None = None,
    ) -> None:
        """``objective`` is the named ObjectiveConfig referenced by
        ``optimize_config.objective``, resolved by the caller from the base
        config. ``storage=None`` keeps the study in memory (tests).
        ``sampler_seed`` overrides the study config's seed (parallel workers
        pass derived per-worker seeds)."""
        self._run_backtest = run_backtest
        self._raw_config = raw_config
        self._config = optimize_config
        self._objective_cfg = objective
        self._symbols = list(symbols)
        self._storage = storage
        self._n_trials = n_trials or optimize_config.study.n_trials
        self._study_name = study_name or optimize_config.study.name
        self._sampler_seed = (
            sampler_seed if sampler_seed is not None else optimize_config.study.sampler_seed
        )
        #: the study of the last optimize() call, for introspection
        self.last_study: optuna.Study | None = None

    def optimize(self) -> OptimizationResult:
        # Fail fast on typo'd search-space paths before any trial runs.
        validate_override_paths(self._raw_config, self._config.search_space)

        seed = self._sampler_seed
        study = optuna.create_study(
            study_name=self._study_name,
            storage=self._storage,
            sampler=optuna.samplers.TPESampler(seed=seed) if seed is not None else None,
            direction="maximize",
            load_if_exists=True,
        )
        if study.trials and seed is not None:
            logger.warning(
                "resuming study %r with a fixed sampler seed; the re-seeded "
                "sampler can skew exploration",
                self._study_name,
            )
        self.last_study = study
        study.optimize(
            self._trial_objective,
            n_trials=self._n_trials,
            catch=(ValidationError, ValueError),
        )
        return summarize(self._study_name, study)

    def _trial_objective(self, trial: optuna.Trial) -> float:
        overrides = suggest_overrides(trial, self._config.search_space)
        raw = apply_overrides(self._raw_config, overrides)
        config = AppConfig.model_validate(raw)  # invalid combination -> trial FAILs

        window = self._config.backtest
        result = self._run_backtest(config, window.start_dt, window.end_exclusive_dt, self._symbols)
        # Link before scoring, so even trials that fail scoring keep their run.
        trial.set_user_attr("run_id", str(result.run_id))

        violations = violated_constraints(result.metrics, self._config.constraints)
        if violations:
            # COMPLETE but heavily penalized: steers the sampler away without
            # touching the run's honest, persisted fitness.
            trial.set_user_attr("infeasible", True)
            trial.set_user_attr("violated_constraints", violations)
            logger.info("trial %d infeasible: %s", trial.number, "; ".join(violations))
            return self._objective_cfg.penalty_score

        name = fitness_metric_name(self._config.objective)
        fitness = next(
            (m.value for m in result.metrics if m.name == name and m.symbol is None), None
        )
        if fitness is None:
            raise ValueError(f"run {result.run_id} produced no {name!r} metric")
        return fitness


def create_or_load_study(study_name: str, storage_url: str) -> None:
    """Pre-create the study so parallel workers can't race on creation."""
    optuna.create_study(
        study_name=study_name, storage=storage_url, direction="maximize", load_if_exists=True
    )


def summarize_study(study_name: str, storage_url: str) -> OptimizationResult:
    """Summary of a persisted study (the parent process after workers join)."""
    return summarize(study_name, optuna.load_study(study_name=study_name, storage=storage_url))


def summarize(study_name: str, study: optuna.Study) -> OptimizationResult:
    states = [trial.state for trial in study.trials]
    complete = states.count(optuna.trial.TrialState.COMPLETE)
    infeasible = sum(
        1
        for trial in study.trials
        if trial.state == optuna.trial.TrialState.COMPLETE
        and trial.user_attrs.get("infeasible", False)
    )
    best = study.best_trial if complete else None
    best_run_id = best.user_attrs.get("run_id") if best else None
    return OptimizationResult(
        study_name=study_name,
        best_trial_number=best.number if best else None,
        best_score=best.value if best else None,
        best_params=dict(best.params) if best else {},
        best_run_id=UUID(best_run_id) if best_run_id else None,
        n_complete=complete,
        n_failed=states.count(optuna.trial.TrialState.FAIL),
        n_infeasible=infeasible,
    )
