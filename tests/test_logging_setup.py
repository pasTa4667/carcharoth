from datetime import UTC, datetime
from uuid import uuid4

import yaml

from carcharoth.config.loader import load_profile
from carcharoth.logging_setup import write_backtest_summary


def test_backtest_summary_contains_model_and_risk(tmp_path) -> None:
    config = load_profile("training").config
    run_id = uuid4()
    write_backtest_summary(
        tmp_path,
        run_id,
        datetime.now(UTC),
        config.model,
        config.risk,
        [],
        config_hash="abc",
    )
    document = yaml.safe_load((tmp_path / "backtests" / f"{run_id}.yaml").read_text())
    assert document["config"]["model"]["benchmark_symbol"] == "SPY"
    assert document["config_hash"] == "abc"
