from datetime import UTC, datetime, timedelta

from carcharoth.config.loader import config_hash, load_profile
from carcharoth.config.run_config import RunConfig
from carcharoth.domain.models import Bar
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
    )
    assert artifact.parent == tmp_path
    assert (artifact / MODEL_FILENAME).is_file()
    assert (artifact / MANIFEST_FILENAME).is_file()
    assert (artifact / REPORT_FILENAME).is_file()
