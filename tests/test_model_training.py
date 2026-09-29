import json
from datetime import UTC, datetime, timedelta

from carcharoth.config.loader import config_hash, load_profile
from carcharoth.config.run_config import RunConfig
from carcharoth.domain.models import Bar
from carcharoth.features.session import MARKET_TZ, TradingSession
from carcharoth.modeling.artifact import MANIFEST_FILENAME, MODEL_FILENAME, REPORT_FILENAME
from carcharoth.modeling.training import train_and_publish


def session(symbol: str, day: int) -> list[Bar]:
    start = datetime(2024, 1, day, 14, 0, tzinfo=UTC)
    return [
        Bar(
            symbol,
            start + timedelta(minutes=5 * index),
            100 + index,
            101 + index,
            99 + index,
            100.5 + index,
            1000 + index,
        )
        for index in range(30)
    ]


def test_train_publishes_immutable_artifact(tmp_path) -> None:
    raw = load_profile("training").config.model_dump(mode="python")
    raw["symbols"] = ["AAPL"]
    raw["model"]["features"] = [{"name": "log_return", "params": {"period": 1}}]
    raw["model"]["target"] = {"type": "regression", "horizon_bars": 2}
    raw["model"]["xgboost_params"] = {"num_boost_round": 10, "max_depth": 2}
    raw["training"] = {
        "train_start": "2024-01-02",
        "train_end": "2024-01-02",
        "validation_start": "2024-01-03",
        "validation_end": "2024-01-03",
        "artifact_root": tmp_path,
        "seed": 1,
        "early_stopping_rounds": 3,
        "eligibility": {"min_train_rows": 5, "min_validation_rows": 5, "min_usable_coverage": 0.2},
    }
    config = RunConfig.model_validate(raw)
    all_bars = {
        "AAPL": session("AAPL", 2) + session("AAPL", 3),
        "SPY": session("SPY", 2) + session("SPY", 3),
    }

    artifact = train_and_publish(
        config,
        config_hash(config),
        lambda symbols, timeframe, start, end: all_bars,
        lambda start, end: [
            TradingSession(
                datetime(2024, 1, day, 9, 30, tzinfo=MARKET_TZ),
                datetime(2024, 1, day, 16, tzinfo=MARKET_TZ),
            )
            for day in (2, 3)
        ],
    )
    assert artifact.parent == tmp_path
    assert (artifact / MODEL_FILENAME).is_file()
    assert (artifact / MANIFEST_FILENAME).is_file()
    report_path = artifact / REPORT_FILENAME
    assert report_path.is_file()
    report = json.loads(report_path.read_text())
    assert report["model_id"] == artifact.name
    assert report["task"] == "regression"
    assert report["diagnostics"]["scope"] == "validation"
    assert report["rows_by_symbol"]["train"]["AAPL"] > 0
    assert (
        sum(bucket["count"] for bucket in report["diagnostics"]["prediction_deciles"])
        == report["validation"]["complete_rows"]
    )
