import copy
import json
from datetime import UTC, datetime
from uuid import uuid4

from carcharoth.modeling.backtest_eligibility import report_backtest_eligibility
from tests.test_training_eligibility import inputs


def test_report_only_uses_features_not_future_labels_and_preserves_inputs(tmp_path):
    config, bars, calendar = inputs(tmp_path)
    config.model.artifact_path = None
    config.model.target.horizon_bars = 500
    before = copy.deepcopy(bars)
    symbols = list(config.symbols)
    report = report_backtest_eligibility(
        config,
        bars,
        symbols,
        datetime(2024, 1, 3, tzinfo=UTC),
        datetime(2024, 1, 4, tzinfo=UTC),
        uuid4(),
        lambda *args: calendar,
        tmp_path,
    )
    assert report.scope == "backtest"
    assert report.status == "warning"
    assert report.effective_symbols == symbols
    assert report.symbols[0].windows["backtest"].usable_rows > 0
    assert report.symbols[1].decision == "warning"
    assert all(symbol.trained is None for symbol in report.symbols)
    assert bars == before
    assert config.symbols == symbols
    assert report.horizon_bars == 0


def test_unavailable_calendar_is_a_durable_diagnostic_error(tmp_path):
    config, bars, _ = inputs(tmp_path)
    run_id = uuid4()
    report = report_backtest_eligibility(
        config,
        bars,
        config.symbols,
        datetime(2024, 1, 3, tzinfo=UTC),
        datetime(2024, 1, 4, tzinfo=UTC),
        run_id,
        None,
        tmp_path,
    )
    assert report.status == "error"
    assert report.symbols == []
    assert report.outcome == "reported"
    assert json.loads((tmp_path / f"{run_id}.eligibility.json").read_text())["error"]
