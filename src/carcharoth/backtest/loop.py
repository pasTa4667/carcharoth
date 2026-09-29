"""Calendar-anchored, expanding-window research loops (no trading services here)."""

from calendar import monthrange
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from carcharoth.config.loader import ResolvedConfig, load_profile
from carcharoth.domain.models import BacktestResult
from carcharoth.features.pipeline import FeaturePipeline
from carcharoth.modeling.artifact import MANIFEST_FILENAME, load_manifest, validate_artifact

PERIODS = {"quarterly": 3, "half-year": 6, "yearly": 12}
TRAIN_DATES = ("train_start", "train_end", "validation_start", "validation_end")


def add_months(anchor: date, months: int) -> date:
    """Always offset the original anchor, not an already clamped boundary."""
    year, month = divmod(anchor.year * 12 + anchor.month - 1 + months, 12)
    return date(year, month + 1, min(anchor.day, monthrange(year, month + 1)[1]))


@dataclass(frozen=True)
class LoopOptions:
    iterations: int
    snapshot: Path
    bucket: str
    backtest_period: str = "quarterly"
    train: bool = False
    train_start: date | None = None
    train_period: str = "yearly"
    validation_period: str = "quarterly"
    artifact: Path | None = None
    start: date | None = None
    prepared_history: bool = False
    label: str = ""
    profile: str = "backtest"

    def schedule(self, today: date | None = None) -> list[dict[str, date]]:
        if self.iterations < 1:
            raise ValueError("iterations must be a positive integer")
        if not self.bucket.strip():
            raise ValueError("a universe bucket is required")
        periods = [self.backtest_period]
        if self.train:
            periods.extend((self.train_period, self.validation_period))
        if any(period not in PERIODS for period in periods):
            raise ValueError("period must be quarterly, half-year, or yearly")
        anchor = self.train_start if self.train else self.start
        if anchor is None:
            raise ValueError(
                "train start is required" if self.train else "backtest start is required"
            )
        if not self.train and self.artifact is None:
            raise ValueError("a model artifact is required when training is off")
        today = today or datetime.now(UTC).date()
        step = PERIODS[self.backtest_period]
        offset = PERIODS[self.train_period] + PERIODS[self.validation_period] if self.train else 0
        # Check the final boundary first, before allocating a potentially enormous schedule.
        try:
            final = add_months(anchor, offset + self.iterations * step)
        except (ValueError, OverflowError) as exc:
            raise ValueError("loop dates exceed the supported calendar") from exc
        if final > today:
            raise ValueError("all backtest windows must end before today (UTC)")
        windows = []
        day = timedelta(days=1)
        for i in range(self.iterations):
            window = {
                "data.start": add_months(anchor, offset + i * step),
                "data.end": add_months(anchor, offset + (i + 1) * step) - day,
            }
            if self.train:
                validation = add_months(anchor, PERIODS[self.train_period] + i * step)
                window.update(
                    {
                        "training.train_start": anchor,
                        "training.train_end": validation - day,
                        "training.validation_start": validation,
                        "training.validation_end": window["data.start"] - day,
                    }
                )
            windows.append(window)
        return windows


def plan_loop(
    options: LoopOptions,
    overrides: Mapping[str, Any] | None = None,
    *,
    today: date | None = None,
) -> list[ResolvedConfig]:
    """Validate the entire schedule locally, before any fetch, train or DB call."""
    windows = options.schedule(today)
    owned: dict[str, Any] = {
        "universe.snapshot": str(options.snapshot),
        "universe.bucket": options.bucket,
        "model.prepared_history": options.prepared_history,
        "model.artifact_path": None if options.train else str(options.artifact),
    }
    if not options.train:
        assert options.artifact is not None
        manifest = load_manifest(options.artifact / MANIFEST_FILENAME)
        owned.update({f"training.{key}": getattr(manifest, key) for key in TRAIN_DATES})
    plans = [
        load_profile(options.profile, {**(overrides or {}), **owned, **window})
        for window in windows
    ]
    if not options.train:
        assert options.artifact is not None
        # Later starts are strictly later and every iteration has the same model/universe.
        config = plans[0].config
        model = config.model
        validate_artifact(
            options.artifact,
            FeaturePipeline(model.features, model.timeframe_minutes, model.benchmark_symbol),
            model,
            evaluation_start=config.data.start,
            evaluation_universe=config.universe_metadata,
        )
    return plans


def execute_loop(
    options: LoopOptions,
    plans: list[ResolvedConfig],
    train: Callable[[ResolvedConfig, dict[str, Any]], Path],
    backtest: Callable[[ResolvedConfig, dict[str, Any]], BacktestResult],
) -> list[BacktestResult]:
    """Fail fast; only a successful training return can select the next artifact."""
    loop_id = str(uuid4())
    print(f"loop_id: {loop_id}  label: {options.label}", flush=True)
    results = []
    for i, resolved in enumerate(plans, 1):
        prefix = f"[{loop_id} iteration {i}/{options.iterations}]"
        context: dict[str, Any] = {
            "loop_id": loop_id, "label": options.label,
            "iteration": i, "iterations": options.iterations,
        }
        if options.train:
            dates = resolved.config.training
            print(
                f"{prefix} training {dates.train_start}..{dates.train_end}; "
                f"validation {dates.validation_start}..{dates.validation_end}",
                flush=True,
            )
            trained_path = train(resolved, dict(context))
            resolved = load_profile(
                resolved.profile, {**resolved.overrides, "model.artifact_path": str(trained_path)}
            )
        path = resolved.config.model.artifact_path
        assert path is not None
        manifest = load_manifest(path / MANIFEST_FILENAME)
        context["model_id"] = manifest.model_id
        print(
            f"{prefix} backtest {resolved.config.data.start}..{resolved.config.data.end}; "
            f"artifact: {path}",
            flush=True,
        )
        result = backtest(resolved, context)
        results.append(result)
        print(f"{prefix} complete; run_id: {result.run_id}", flush=True)
    print(f"loop {loop_id} complete ({len(results)} backtests)", flush=True)
    return results


def preview(options: LoopOptions, plans: list[ResolvedConfig]) -> dict[str, Any]:
    return {
        "train": options.train,
        "label": options.label,
        "iterations": options.iterations,
        "windows": [
            {
                "iteration": i,
                "training": resolved.config.training.model_dump(mode="json"),
                "backtest": resolved.config.data.model_dump(mode="json"),
                "artifact": resolved.config.model.model_dump(mode="json")["artifact_path"],
                "universe": resolved.config.universe.model_dump(mode="json"),
                "symbol_count": len(resolved.config.symbols),
                "prepared_history": resolved.config.model.prepared_history,
                "config_hash": resolved.hash,
            }
            for i, resolved in enumerate(plans, 1)
        ],
    }
