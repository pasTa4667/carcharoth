"""Bounded, context-local backtest measurements; no trading dependencies.

The composition root injects a Monitor for one execution. Stage helpers are
inactive outside that scope, including live trading and optimizer workers.
Only checkpoints publish: stage exit never performs I/O. Inclusive parent
measurements include nested work; exclusive measurements subtract it.
"""

from __future__ import annotations

import cProfile
import json
import logging
import math
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, TypeVar
from uuid import UUID, uuid4

from carcharoth.domain.models import PerformanceSnapshot, PerformanceStage

logger = logging.getLogger(__name__)
P = ParamSpec("P")
T = TypeVar("T")
_CURRENT: ContextVar[Monitor | None] = ContextVar("backtest_performance", default=None)
STAGES = frozenset(
    {
        "setup",
        "data_fetch",
        "feature_preparation",
        "hmm_preparation",
        "cache_read",
        "cache_decode",
        "cache_fetch",
        "cache_write",
        "simulation",
        "final_flush",
        "analysis",
        "summary",
        "market_data",
        "broker",
        "reconcile",
        "account",
        "snapshot_persistence",
        "model",
        "risk",
        "execution",
        "decision_persistence",
        "logging",
        "features",
        "prediction",
        "policy",
        "repository",
        "buffer_flush",
    }
)
COUNTERS = frozenset(
    {
        "ticks",
        "total_ticks",
        "symbol_attempts",
        "features",
        "feature_matrices",
        "preparation_rows",
        "hmm_context_hits",
        "hmm_context_misses",
        "hmm_assessments",
        "cache_chunk_hits",
        "cache_chunk_misses",
        "cache_bytes_read",
        "cache_bytes_written",
        "cache_bytes_decoded",
        "predictions",
        "repository_calls",
        "symbol_errors",
    }
)


@dataclass
class _Frame:
    path: str
    start: float
    children: float = 0.0


class Monitor:
    """Single-threaded collection, injected clocks/publisher, bounded stage keys.

    Use execution() once; checkpoint() is safe at replay boundaries. Publisher
    receives immutable dataclasses with copied mappings, never live accumulators.
    Wall totals include telemetry I/O; stage clocks explicitly exclude it.
    """

    def __init__(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        interval: float = 0,
        publish: Callable[[PerformanceSnapshot], None] | None = None,
        output_dir: Path | None = None,
        clock: Callable[[], float] = time.perf_counter,
        cpu_clock: Callable[[], float] = time.process_time,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("performance interval must be finite and non-negative")
        self.execution_id = uuid4()
        self.run_id: UUID | None = None
        self.metadata = {"measurement_version": 1, **(metadata or {})}
        self.interval = interval
        self._publish = publish
        self._output_dir = output_dir
        self._clock, self._cpu, self._utcnow = clock, cpu_clock, utcnow
        self._frames: list[_Frame] = []
        self._stages: dict[str, PerformanceStage] = {}
        self.counters: dict[str, int] = {}
        self._telemetry = 0.0
        self._errors = 0
        self._periodic = interval > 0 and publish is not None
        self._simulation_cpu = 0.0
        self._simulation_cpu_start: float | None = None
        self._status = "RUNNING"
        self._start = self._cpu_start = self._last_checkpoint = 0.0
        self._recent_clock = 0.0
        self._recent_ticks = 0
        self._recent_rate: float | None = None
        self._started_at = utcnow()
        self._finished_at: datetime | None = None
        self._used = False
        self._end: float | None = None
        self._cpu_end: float | None = None

    @contextmanager
    def execution(self) -> Iterator[Monitor]:
        if self._used or _CURRENT.get() is not None:
            raise RuntimeError("performance execution cannot be reused or nested")
        self._used = True
        self._start = self._clock()
        self._cpu_start = self._cpu()
        self._started_at = self._utcnow()
        self._recent_clock = self._last_checkpoint = self._start
        token = _CURRENT.set(self)
        try:
            try:
                self._publication()
                yield self
            except (KeyboardInterrupt, SystemExit):
                self._status = "INTERRUPTED"
                raise
            except BaseException:
                self._status = "FAILED"
                raise
            else:
                self._status = "SUCCEEDED"
        finally:
            try:
                self._finished_at = self._utcnow()
                self._publication()
                self._end, self._cpu_end = self._clock(), self._cpu()
                self._write_report()
            finally:
                _CURRENT.reset(token)

    def increment(self, name: str, amount: int = 1) -> None:
        if name not in COUNTERS or amount < 0:
            raise ValueError("unknown counter or negative increment")
        self.counters[name] = self.counters.get(name, 0) + amount

    def _work_clock(self) -> float:
        return self._clock() - self._telemetry

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if name not in STAGES or len(self._frames) >= 8:
            raise ValueError("unknown stage or excessive nesting")
        path = "/".join([*(frame.path.rsplit("/", 1)[-1] for frame in self._frames), name])
        if path not in self._stages and len(self._stages) >= 128:
            raise ValueError("too many stage paths")
        frame = _Frame(path, self._work_clock())
        self._frames.append(frame)
        if name == "simulation":
            self._simulation_cpu_start = self._cpu()
            self._recent_clock = self._clock()
            self._recent_ticks = self.counters.get("ticks", 0)
        try:
            yield
        finally:
            duration = max(0.0, self._work_clock() - frame.start)
            self._frames.pop()
            old = self._stages.get(path, PerformanceStage(path, 0, 0, 0, 0))
            self._stages[path] = PerformanceStage(
                path,
                old.calls + 1,
                old.inclusive_seconds + duration,
                old.exclusive_seconds + max(0.0, duration - frame.children),
                max(old.max_seconds, duration),
            )
            if self._frames:
                self._frames[-1].children += duration
            if name == "simulation" and self._simulation_cpu_start is not None:
                self._simulation_cpu += max(0.0, self._cpu() - self._simulation_cpu_start)
                self._simulation_cpu_start = None

    def snapshot(self) -> PerformanceSnapshot:
        now = self._work_clock()
        stages = dict(self._stages)
        for index, frame in enumerate(self._frames):
            duration = max(0.0, now - frame.start)
            active_child = (
                max(0.0, now - self._frames[index + 1].start)
                if index + 1 < len(self._frames)
                else 0.0
            )
            old = stages.get(frame.path, PerformanceStage(frame.path, 0, 0, 0, 0))
            stages[frame.path] = replace(
                old,
                calls=old.calls + 1,
                inclusive_seconds=old.inclusive_seconds + duration,
                exclusive_seconds=old.exclusive_seconds
                + max(0.0, duration - frame.children - active_child),
                max_seconds=max(old.max_seconds, duration),
            )
        simulation_cpu = self._simulation_cpu
        if self._simulation_cpu_start is not None:
            simulation_cpu += max(0.0, self._cpu() - self._simulation_cpu_start)
        return PerformanceSnapshot(
            self.execution_id,
            self.run_id,
            self._status,
            self._started_at,
            self._utcnow(),
            self._finished_at,
            max(0.0, (self._end if self._end is not None else self._clock()) - self._start),
            max(
                0.0, (self._cpu_end if self._cpu_end is not None else self._cpu()) - self._cpu_start
            ),
            simulation_cpu,
            self._telemetry,
            self._periodic,
            self._errors,
            dict(self.counters),
            dict(self.metadata),
            tuple(stages[key] for key in sorted(stages)),
            self._recent_rate,
        )

    def checkpoint(self) -> None:
        now = self._clock()
        if not self._periodic or now - self._last_checkpoint < self.interval:
            return
        self._last_checkpoint = now
        ticks = self.counters.get("ticks", 0)
        elapsed = now - self._recent_clock
        self._recent_rate = (ticks - self._recent_ticks) / elapsed if elapsed > 0 else None
        self._recent_clock, self._recent_ticks = now, ticks
        self._publication()

    def _publication(self) -> None:
        if self._publish is None:
            return
        start = self._clock()
        failed = False
        try:
            self._publish(self.snapshot())
        except Exception:
            self._errors += 1
            failed = True
            if self._errors == 1:
                logger.warning(
                    "performance publication failed; retaining local report", exc_info=True
                )
        finally:
            elapsed = max(0.0, self._clock() - start)
            self._telemetry += elapsed
            if self._periodic and (failed or elapsed > 0.1):
                self._periodic = False
                logger.warning("periodic performance publication disabled (%.3fs)", elapsed)

    def _write_report(self) -> None:
        try:
            snapshot = self.snapshot()
            if self._output_dir is not None:
                self._output_dir.mkdir(parents=True, exist_ok=True)
                path = self._output_dir / f"{self.execution_id}.performance.json"
                path.write_text(json.dumps(asdict(snapshot), default=str, indent=2) + "\n")
            slowest = sorted(snapshot.stages, key=lambda s: s.exclusive_seconds, reverse=True)[:5]
            logger.info(
                "performance %s: %s",
                self.execution_id,
                ", ".join(f"{s.path}={s.exclusive_seconds:.3f}s" for s in slowest),
            )
        except Exception:
            logger.warning("could not write performance summary", exc_info=True)


def stage(name: str) -> AbstractContextManager[None]:
    """Context-local stage, inactive unless composition injected a Monitor."""
    monitor = _CURRENT.get()
    return monitor.stage(name) if monitor is not None else nullcontext()


def measured(name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorate(fn: Callable[P, T]) -> Callable[P, T]:
        @wraps(fn)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            monitor = _CURRENT.get()
            if monitor is None:
                return fn(*args, **kwargs)
            if name == "repository":
                monitor.increment("repository_calls")
            with monitor.stage(name):
                return fn(*args, **kwargs)

        return wrapped

    return decorate


def increment(name: str, amount: int = 1) -> None:
    monitor = _CURRENT.get()
    if monitor is not None:
        monitor.increment(name, amount)


def checkpoint() -> None:
    monitor = _CURRENT.get()
    if monitor is not None:
        monitor.checkpoint()


@contextmanager
def replay_profile(path: Path | None) -> Iterator[None]:
    """Opt-in deterministic replay profile; dump failures never mask workload errors."""
    if path is None:
        yield
        return
    if sys.getprofile() is not None:
        logger.warning("replay profiler skipped: a profiler is already active")
        yield
        return
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        yield
    finally:
        profiler.disable()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            profiler.dump_stats(str(path))
        except Exception:
            logger.warning("could not write replay profile %s", path, exc_info=True)
