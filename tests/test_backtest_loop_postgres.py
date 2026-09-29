"""Opt-in dashboard SQL and persistence checks on a migrated disposable PostgreSQL DB."""

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from carcharoth.config.loader import load_profile
from carcharoth.domain.models import RunType
from carcharoth.persistence.db import build_engine, build_session_factory
from carcharoth.persistence.orm import BacktestMetricRow, EquitySnapshotRow
from carcharoth.persistence.repositories import SqlAlchemyRunRepository

DASHBOARD = Path("grafana/provisioning/dashboards/backtest-loops.json")


def test_dashboard_structure_and_safe_variables():
    dashboard = json.loads(DASHBOARD.read_text())
    assert dashboard["uid"] == "backtest-loops"
    assert len({panel["id"] for panel in dashboard["panels"]}) == len(dashboard["panels"])
    for panel in dashboard["panels"]:
        for target in panel["targets"]:
            sql = target["rawSql"]
            assert "${loop:sqlstring}" in sql
            assert "$__timeFilter" not in sql
    assert dashboard["time"]["from"].startswith("2016")


def test_grouped_dashboard_sql_and_nullable_context():
    url = os.environ.get("CARCHAROTH_LOOP_TEST_URL")
    if not url:
        pytest.skip("requires explicitly selected migrated disposable PostgreSQL database")
    engine = build_engine(url)
    factory = build_session_factory(engine)
    repo = SqlAlchemyRunRepository(factory)
    run_ids = []
    loops = [str(uuid4()), str(uuid4())]
    config = load_profile("backtest").config.model_dump(mode="json")
    config["backtest"]["initial_capital"] = 100
    historical = datetime(2021, 1, 4, tzinfo=UTC)
    dashboard = json.loads(DASHBOARD.read_text())
    try:
        for group, iteration, complete in [(0, 1, True), (0, 2, False), (1, 1, True)]:
            context = {
                "loop_id": loops[group],
                "label": "same 'quoted' label",
                "iteration": iteration,
                "iterations": 2,
                "model_id": "fixture",
            }
            run_id = repo.create(
                RunType.BACKTEST, config, ["SPY"], datetime.now(UTC), loop_context=context
            )
            run_ids.append(run_id)
            assert repo.get(run_id).loop_context == context
            repo.finish(run_id, datetime.now(UTC))  # finished_at alone does not mean analyzed
            if complete:
                with factory.begin() as session:
                    session.add_all(
                        [
                            BacktestMetricRow(
                                run_id=run_id, name="total_return", symbol=None, value=Decimal("0")
                            ),
                            BacktestMetricRow(
                                run_id=run_id, name="num_trades", symbol=None, value=Decimal("0")
                            ),
                            BacktestMetricRow(
                                run_id=run_id,
                                name="total_return",
                                symbol="SPY",
                                value=Decimal("999"),
                            ),
                            EquitySnapshotRow(
                                run_id=run_id,
                                timestamp=historical,
                                equity=Decimal(100),
                                cash=Decimal(100),
                                buying_power=Decimal(100),
                            ),
                        ]
                    )
        plain_id = repo.create(RunType.BACKTEST, config, ["SPY"], datetime.now(UTC))
        run_ids.append(plain_id)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT loop_context IS NULL FROM runs WHERE run_id=:id"), {"id": plain_id}
            ).scalar_one()
            choices = (
                connection.execute(text(dashboard["templating"]["list"][0]["query"]))
                .mappings()
                .all()
            )
            assert {row["__value"] for row in choices} >= set(loops)
            panels = {}
            for panel in dashboard["panels"]:
                sql = panel["targets"][0]["rawSql"].replace("${loop:sqlstring}", ":loop_id")
                panels[panel["id"]] = (
                    connection.execute(text(sql), {"loop_id": loops[0]}).mappings().all()
                )
            assert panels[1][0] == {"requested": 2, "recorded": 2, "completed": 1}
            assert [row["iteration"] for row in panels[2]] == [1, 2]
            assert panels[2][0]["total_return"] == 0  # not the per-symbol value
            assert panels[2][0]["num_trades"] == 0
            assert panels[2][1]["state"] == "running / unfinished"
            assert len(panels[3]) == 2
            assert len(panels[4]) == 1
            assert panels[4][0]["time"] == historical
            assert panels[4][0]["value"] == 0  # normalized by configured initial capital
    finally:
        for run_id in run_ids:
            repo.delete(run_id)
        engine.dispose()
