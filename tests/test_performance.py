from dataclasses import dataclass
from pathlib import Path

import pytest

from carcharoth.domain.models import PerformanceSnapshot
from carcharoth.performance import Monitor, increment, measured, replay_profile, stage
from tests.fakes import InMemoryPerformanceRepository


@dataclass
class Clock:
    value: float = 0

    def __call__(self) -> float:
        return self.value


def test_nested_and_active_snapshot() -> None:
    clock = Clock()
    monitor = Monitor(clock=clock, cpu_clock=clock)
    with monitor.execution():
        with stage("simulation"):
            clock.value = 1
            with stage("model"):
                clock.value = 3
                snap = monitor.snapshot()
                assert {s.path: s.exclusive_seconds for s in snap.stages} == {
                    "simulation": 1,
                    "simulation/model": 2,
                }
            clock.value = 4
            with stage("model"):
                clock.value = 5
        increment("ticks", 2)
    snap = monitor.snapshot()
    stages = {s.path: s for s in snap.stages}
    assert stages["simulation"].inclusive_seconds == 5
    assert stages["simulation"].exclusive_seconds == 2
    assert stages["simulation/model"].calls == 2
    assert stages["simulation/model"].max_seconds == 2
    assert snap.simulation_cpu_seconds == 5
    assert snap.counters == {"ticks": 2}
    assert snap.status == "SUCCEEDED"


def test_checkpoint_rate_and_no_stage_writes() -> None:
    clock = Clock()
    repository = InMemoryPerformanceRepository()
    rows = repository.snapshots
    monitor = Monitor(clock=clock, interval=10, publish=repository.publish)
    with monitor.execution():
        for _ in range(30):
            with stage("simulation"):
                clock.value += 1
                increment("ticks")
            assert len(rows) == 1
        monitor.checkpoint()
        monitor.checkpoint()
        assert len(rows) == 2
        assert rows[-1].recent_ticks_per_second == 1
    assert len(rows) == 3
    assert rows[-1].status == "SUCCEEDED"


@pytest.mark.parametrize("failure", [False, True])
def test_slow_or_failed_publication_disables_periodic(failure: bool, tmp_path: Path) -> None:
    clock = Clock()
    calls = 0

    def publish(snapshot: PerformanceSnapshot) -> None:
        nonlocal calls
        calls += 1
        clock.value += 0.2
        if failure:
            raise OSError("database unavailable")

    monitor = Monitor(clock=clock, interval=10, publish=publish, output_dir=tmp_path)
    with pytest.raises(ValueError, match="original"), monitor.execution():
        clock.value += 20
        monitor.checkpoint()
        raise ValueError("original")
    assert calls == 2  # initial and final, no retry in replay
    snap = monitor.snapshot()
    assert not snap.periodic_enabled
    assert snap.status == "FAILED"
    assert snap.telemetry_seconds == pytest.approx(0.4)
    assert len(list(tmp_path.glob("*.performance.json"))) == 1


def test_final_only_and_publication_excluded_from_stage() -> None:
    clock = Clock()
    calls: list[PerformanceSnapshot] = []

    def publish(snapshot: PerformanceSnapshot) -> None:
        calls.append(snapshot)
        clock.value += 0.05

    monitor = Monitor(clock=clock, interval=10, publish=publish)
    with monitor.execution(), stage("simulation"):
        clock.value += 11
        monitor.checkpoint()
        clock.value += 1
    assert monitor.snapshot().stages[0].inclusive_seconds == pytest.approx(12)
    assert len(calls) == 3
    calls.clear()
    with Monitor(clock=clock, publish=publish).execution() as monitor:
        clock.value += 100
        monitor.checkpoint()
    assert len(calls) == 2


def test_interrupt_and_scope_cleanup() -> None:
    monitor = Monitor()
    with pytest.raises(KeyboardInterrupt), monitor.execution():
        raise KeyboardInterrupt
    assert monitor.snapshot().status == "INTERRUPTED"
    increment("not a counter")  # disabled outside execution
    with stage("not a stage"):
        pass
    with Monitor().execution(), pytest.raises(RuntimeError), Monitor().execution():
        pass


def test_bounds_and_disabled_decorator() -> None:
    @measured("repository")
    def f(value: int) -> int:
        return value + 1

    assert f(2) == 3
    with Monitor().execution() as monitor:
        for _ in range(1000):
            assert f(2) == 3
        assert monitor.snapshot().stages[0].calls == 1000
        assert monitor.counters["repository_calls"] == 1000
        with pytest.raises(ValueError), stage("symbol:AAPL"):
            pass
        with pytest.raises(ValueError):
            increment("arbitrary")
    for value in [-1, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            Monitor(interval=value)


def test_final_totals_frozen_and_first_recent_rate_excludes_fetch() -> None:
    clock = Clock()
    repository = InMemoryPerformanceRepository()
    monitor = Monitor(clock=clock, cpu_clock=clock, interval=10, publish=repository.publish)
    with monitor.execution():
        clock.value = 30  # lengthy fetch, not part of replay throughput
        with stage("simulation"):
            clock.value += 1
            increment("ticks", 2)
            monitor.checkpoint()
        assert repository.snapshots[-1].recent_ticks_per_second == 2
    clock.value += 100
    assert monitor.snapshot().wall_seconds == 31
    assert monitor.snapshot().cpu_seconds == 31


def test_profile_written_on_error_and_dump_failure_does_not_mask(tmp_path: Path) -> None:
    import pstats
    import sys

    path = tmp_path / "replay.pstats"
    with pytest.raises(ValueError), replay_profile(path):
        sum(range(100))
        raise ValueError("original")
    assert sys.getprofile() is None
    assert pstats.Stats(str(path)).total_calls > 0
    with pytest.raises(ValueError, match="original"), replay_profile(path / "bad"):
        raise ValueError("original")
