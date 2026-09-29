"""Opt-in: CARCHAROTH_ELIGIBILITY_TEST_URL must name a migrated disposable DB."""

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from carcharoth.config.loader import config_hash
from carcharoth.features.session import MARKET_TZ, TradingSession
from carcharoth.main import run_backtest_once
from carcharoth.modeling.eligibility import SymbolAssessment, assess
from carcharoth.modeling.training import train_and_publish
from carcharoth.persistence.db import build_engine, build_session_factory
from carcharoth.persistence.repositories import SqlAlchemyEligibilityRepository
from tests.test_data_eligibility import evidence, report
from tests.test_model_training import session
from tests.test_training_eligibility import inputs

DASHBOARD = Path("grafana/provisioning/dashboards/data-eligibility.json")


@pytest.fixture
def database():
    url = os.environ.get("CARCHAROTH_ELIGIBILITY_TEST_URL")
    if not url:
        pytest.skip("requires an explicitly selected migrated disposable PostgreSQL database")
    engine = build_engine(url)
    try:
        yield engine, build_session_factory(engine)
    finally:
        engine.dispose()


def interpolate(query, **filters):
    values = {
        "scope": "__all",
        "assessment": "__all",
        "model": "",
        "run": "",
        "loop": "",
        "symbol": "",
        "reason": "",
        **filters,
    }
    query = re.sub(r"\$__timeFilter\(([^)]+)\)", r"\1 >= '2000-01-01'", query)
    for name, value in values.items():
        query = query.replace("${" + name + ":sqlstring}", "'" + value.replace("'", "''") + "'")
    assert "${" not in query
    return query


def test_upsert_and_actual_dashboard_queries(database):
    engine, factory = database
    repository = SqlAlchemyEligibilityRepository(factory)
    assessment = assess(
        report(),
        [
            SymbolAssessment(
                symbol="GOOD", windows={"train": evidence(), "validation": evidence()}
            ),
            SymbolAssessment(
                symbol="APC", windows={"train": evidence(), "validation": evidence(0)}
            ),
        ],
    )
    repository.save(assessment)
    repository.save(assessment)
    dashboard = json.loads(DASHBOARD.read_text())
    queries = [
        target["rawSql"] for panel in dashboard["panels"] for target in panel.get("targets", [])
    ]
    queries += [
        variable["query"]
        for variable in dashboard["templating"]["list"]
        if variable["type"] == "query"
    ]
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM data_eligibility_assessments WHERE assessment_id=:id"
                    ),
                    {"id": assessment.assessment_id},
                ).scalar_one()
                == 1
            )
            for filters in (
                {"assessment": str(assessment.assessment_id)},
                {"symbol": "APC", "reason": "no_bars"},
                {"symbol": "O'Brien'; SELECT 1; --"},
            ):
                for query in queries:
                    connection.execute(text(interpolate(query, **filters))).all()
            rows = (
                connection.execute(
                    text(interpolate(queries[0], assessment=str(assessment.assessment_id)))
                )
                .mappings()
                .all()
            )
            assert rows[0]["excluded"] == 1  # three reasons, one excluded symbol
        stale = assessment.model_copy(deep=True)
        stale.updated_at -= timedelta(days=1)
        stale.status = "error"
        repository.save(stale)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT status FROM data_eligibility_assessments WHERE assessment_id=:id"),
                    {"id": assessment.assessment_id},
                ).scalar_one()
                == "passed_with_exclusions"
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM data_eligibility_assessments WHERE assessment_id=:id"),
                {"id": assessment.assessment_id},
            )


def test_real_backtest_coverage_toggle_preserves_trades_and_metrics(
    database, tmp_path, monkeypatch
):
    engine, factory = database
    config, bars, sessions = inputs(tmp_path)
    artifact = train_and_publish(
        config, config_hash(config), lambda *args: bars, lambda *args: sessions
    )
    config.model.artifact_path = artifact
    config.data.start = datetime(2024, 1, 4).date()
    config.data.end = config.data.start
    bars["AAPL"] += session("AAPL", 4)
    sessions += [
        TradingSession(
            datetime(2024, 1, 4, 9, 30, tzinfo=MARKET_TZ),
            datetime(2024, 1, 4, 16, tzinfo=MARKET_TZ),
        )
    ]
    monkeypatch.setattr("carcharoth.main.LOG_DIR", tmp_path / "logs")
    results, trades, counts = [], [], []
    try:
        for enabled in (False, True):
            config.backtest.check_data_coverage = enabled
            calls = []

            def fetch(*args, calls=calls):
                calls.append(args)
                return bars

            result = run_backtest_once(
                config,
                config.data.start_dt,
                config.data.end_exclusive_dt,
                config.symbols,
                factory,
                fetch,
                fetch_calendar=lambda *args: sessions,
                loop_context={"loop_id": str(uuid4()), "iteration": 1},
            )
            results.append(result)
            assert len(calls) == 1
            with engine.connect() as connection:
                trades.append(
                    connection.execute(
                        text(
                            "SELECT symbol, side, qty, price, timestamp "
                            "FROM trades WHERE run_id=:id ORDER BY timestamp, symbol"
                        ),
                        {"id": result.run_id},
                    ).all()
                )
                counts.append(
                    connection.execute(
                        text("SELECT count(*) FROM data_eligibility_assessments WHERE run_id=:id"),
                        {"id": result.run_id},
                    ).scalar_one()
                )
        assert results[0].metrics == results[1].metrics
        assert trades[0] == trades[1]
        assert trades[0]  # not vacuous no-trade parity
        assert counts == [0, 1]
    finally:
        with engine.begin() as connection:
            for result in results:
                connection.execute(text("DELETE FROM runs WHERE run_id=:id"), {"id": result.run_id})
