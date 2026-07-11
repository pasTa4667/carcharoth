"""Maps the config-driven search space onto Optuna's suggest API."""

from typing import Any

import optuna

from carcharoth.config.optimize_config import (
    CategoricalParam,
    FloatParam,
    IntParam,
    SearchParam,
)


def suggest_overrides(
    trial: optuna.trial.BaseTrial, search_space: dict[str, SearchParam]
) -> dict[str, Any]:
    """One suggested value per search-space entry, keyed by the dot-path."""
    overrides: dict[str, Any] = {}
    for path, param in search_space.items():
        overrides[path] = _suggest(trial, path, param)
    return overrides


def _suggest(trial: optuna.trial.BaseTrial, path: str, param: SearchParam) -> Any:
    match param:
        case IntParam():
            return trial.suggest_int(path, param.low, param.high, step=param.step)
        case FloatParam():
            return trial.suggest_float(path, param.low, param.high, step=param.step, log=param.log)
        case CategoricalParam():
            return trial.suggest_categorical(path, param.choices)
