"""Opt-in integration checks; set CARCHAROTH_PERFORMANCE_TEST_URL to a migrated disposable DB."""

import os
from dataclasses import replace

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from carcharoth.performance import Monitor, stage
from carcharoth.persistence.db import build_performance_engine, build_session_factory
from carcharoth.persistence.repositories import SqlAlchemyPerformanceRepository


def test_postgres_atomic_upsert_and_timeouts() -> None:
    url = os.environ.get("CARCHAROTH_PERFORMANCE_TEST_URL")
    if not url:
        pytest.skip("requires an explicitly selected migrated disposable PostgreSQL database")
    engine = build_performance_engine(url)
    repository = SqlAlchemyPerformanceRepository(build_session_factory(engine))
    monitor = Monitor()
    with monitor.execution(), stage("simulation"):
        monitor.increment("ticks")
    snapshot = monitor.snapshot()
    try:
        repository.publish(snapshot)
        repository.publish(replace(snapshot, counters={"ticks": 2}))
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT counters->>'ticks' FROM backtest_performance WHERE execution_id=:id"
                    ),
                    {"id": snapshot.execution_id},
                ).scalar_one()
                == "2"
            )
            assert connection.execute(text("SHOW statement_timeout")).scalar_one() == "100ms"
            with pytest.raises(DBAPIError):
                connection.execute(text("SELECT pg_sleep(0.3)"))

        def fail_stage_write(*args: object) -> None:
            if str(args[2]).startswith("INSERT INTO backtest_performance_stages"):
                raise OSError("simulated stage failure")

        event.listen(engine, "before_cursor_execute", fail_stage_write)
        try:
            with pytest.raises(OSError):
                repository.publish(replace(snapshot, counters={"ticks": 999}))
        finally:
            event.remove(engine, "before_cursor_execute", fail_stage_write)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT counters->>'ticks' FROM backtest_performance WHERE execution_id=:id"
                    ),
                    {"id": snapshot.execution_id},
                ).scalar_one()
                == "2"
            )  # header rollback too
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM backtest_performance WHERE execution_id=:id"),
                {"id": snapshot.execution_id},
            )
        engine.dispose()
