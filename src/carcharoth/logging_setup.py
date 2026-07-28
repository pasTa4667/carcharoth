"""Logging configuration: app.log, errors.log, trades.log, decisions.log.

Loggers:
- ``carcharoth.*``           -> app.log + console (INFO), errors.log (ERROR)
- ``carcharoth.trades``      -> additionally trades.log (order submits and fills)
- ``carcharoth.decisions``   -> decisions.log only (high volume, does not propagate)
"""

import logging
import logging.config
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import yaml

from carcharoth.analysis.objective import FITNESS_PREFIX
from carcharoth.config.app_config import RegimeConfig, RiskConfig
from carcharoth.domain.models import MetricValue, OptimizationResult

if TYPE_CHECKING:
    from carcharoth.config.quicktest_config import QuickTestConfig

TRADES_LOGGER = "carcharoth.trades"
DECISIONS_LOGGER = "carcharoth.decisions"

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging(log_dir: Path, console_level: str = "INFO", filename_suffix: str = "") -> None:
    """``filename_suffix`` (e.g. ``".w0"``) gives a process its own log files:
    rotating the same file from several processes corrupts it, so parallel
    optimize workers each log to ``app.w<i>.log`` etc."""
    log_dir.mkdir(parents=True, exist_ok=True)

    def file_handler(filename: str, level: str = "INFO") -> dict[str, object]:
        stem, dot, ext = filename.partition(".")
        return {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_dir / f"{stem}{filename_suffix}{dot}{ext}"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "standard",
            "level": level,
        }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"standard": {"format": _FORMAT}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                    "level": console_level,
                },
                "app_file": file_handler("app.log"),
                "errors_file": file_handler("errors.log", level="ERROR"),
                "trades_file": file_handler("trades.log"),
                "decisions_file": file_handler("decisions.log"),
            },
            "loggers": {
                "carcharoth": {
                    "level": "INFO",
                    "handlers": ["console", "app_file", "errors_file"],
                },
                TRADES_LOGGER: {
                    "level": "INFO",
                    "handlers": ["trades_file"],
                    "propagate": True,
                },
                DECISIONS_LOGGER: {
                    "level": "INFO",
                    "handlers": ["decisions_file"],
                    "propagate": False,
                },
            },
        }
    )


def write_backtest_summary(
    log_dir: Path,
    run_id: UUID,
    started_at: datetime,
    regime_cfg: RegimeConfig | None,
    risk_cfg: RiskConfig,
    strategy_cfgs: dict[str, Any],
    metrics: list[MetricValue],
) -> None:
    backtest_dir = log_dir / "backtests"
    backtest_dir.mkdir(parents=True, exist_ok=True)

    flat: dict[str, float] = {}
    per_symbol: dict[str, float] = {}
    fitness: dict[str, float] = {}
    for m in metrics:
        if m.symbol is not None:
            per_symbol[m.symbol] = m.value
        elif m.name.startswith(FITNESS_PREFIX):
            fitness[m.name.removeprefix(FITNESS_PREFIX)] = m.value
        else:
            flat[m.name] = m.value
    results: dict[str, object] = {**flat}
    if per_symbol:
        results["per_symbol"] = per_symbol

    config_block: dict[str, object] = {}
    config_block["strategies"] = strategy_cfgs
    if regime_cfg is not None:
        config_block["regime"] = regime_cfg.model_dump()
    config_block["risk"] = risk_cfg.model_dump()

    summary: dict[str, object] = {
        "run_id": str(run_id),
        "date": started_at.isoformat(),
        "config": config_block,
        "results": results,
    }
    if fitness:
        summary["fitness"] = fitness

    with open(backtest_dir / f"{run_id}.yaml", "w") as f:
        yaml.dump(summary, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def write_quicktest_summary(
    log_dir: Path,
    run_id: UUID,
    started_at: datetime,
    config: "QuickTestConfig",
    metrics: list[MetricValue],
) -> None:
    """Write a human-readable YAML summary of a quick test to
    ``logs/quicktest/{run_id}.yaml`` (mirrors ``write_backtest_summary``).

    ``results`` holds the aggregate metrics plus a ``per_symbol`` map where each
    symbol carries its full metric breakdown (sharpe, profit_factor,
    total_return, max_drawdown, win_rate, num_trades, ...).
    """
    quicktest_dir = log_dir / "quicktest"
    quicktest_dir.mkdir(parents=True, exist_ok=True)

    aggregate: dict[str, float] = {}
    fitness: dict[str, float] = {}
    per_symbol: dict[str, dict[str, float]] = {}
    for m in metrics:
        if m.symbol is not None:
            per_symbol.setdefault(m.symbol, {})[m.name] = m.value
        elif m.name.startswith(FITNESS_PREFIX):
            fitness[m.name.removeprefix(FITNESS_PREFIX)] = m.value
        else:
            aggregate[m.name] = m.value

    results: dict[str, object] = {**aggregate}
    if per_symbol:
        results["per_symbol"] = {sym: per_symbol[sym] for sym in sorted(per_symbol)}

    summary: dict[str, object] = {
        "run_id": str(run_id),
        "date": started_at.isoformat(),
        "config": config.model_dump(mode="json"),
        "results": results,
    }
    if fitness:
        summary["fitness"] = fitness

    with open(quicktest_dir / f"{run_id}.yaml", "w") as f:
        yaml.dump(summary, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def write_optimize_summary(
    log_dir: Path, finished_at: datetime, objective: str, result: OptimizationResult
) -> None:
    """Study-level summary only. Per-trial data lives in Optuna's storage;
    each trial run has its own backtest summary and database rows."""
    optimize_dir = log_dir / "optimize"
    optimize_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, object] = {
        "study": result.study_name,
        "date": finished_at.isoformat(),
        "objective": objective,
        "trials": {
            "complete": result.n_complete,
            "infeasible": result.n_infeasible,
            "failed": result.n_failed,
        },
    }
    if result.best_trial_number is not None:
        summary["best"] = {
            "trial": result.best_trial_number,
            "score": result.best_score,
            "run_id": str(result.best_run_id) if result.best_run_id else None,
            "params": dict(result.best_params),
        }

    with open(optimize_dir / f"{result.study_name}.yaml", "w") as f:
        yaml.dump(summary, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
