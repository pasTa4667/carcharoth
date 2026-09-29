import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from carcharoth.config.loader import config_hash, load_profile
from carcharoth.config.run_config import RunConfig
from carcharoth.features.session import MARKET_TZ, TradingSession
from carcharoth.modeling.artifact import load_manifest
from carcharoth.modeling.assessment_io import EligibilityError
from carcharoth.modeling.training import train_and_publish
from tests.test_model_training import session


def inputs(tmp_path, mode="drop"):
    raw = load_profile("training").config.model_dump(mode="python")
    raw["symbols"] = ["AAPL", "APC"]
    raw["model"]["features"] = [{"name": "log_return", "params": {"period": 1}}]
    raw["model"]["target"] = {"type": "regression", "horizon_bars": 2}
    raw["model"]["xgboost_params"] = {"num_boost_round": 3}
    raw["training"].update(
        train_start="2024-01-02",
        train_end="2024-01-02",
        validation_start="2024-01-03",
        validation_end="2024-01-03",
        artifact_root=tmp_path,
        eligibility={
            "mode": mode,
            "min_train_rows": 5,
            "min_validation_rows": 5,
            "min_usable_coverage": 0.2,
        },
    )
    config = RunConfig.model_validate(raw)
    bars = {"AAPL": session("AAPL", 2) + session("AAPL", 3), "APC": session("APC", 2)}
    calendar = [
        TradingSession(
            datetime(2024, 1, day, 9, 30, tzinfo=MARKET_TZ),
            datetime(2024, 1, day, 16, tzinfo=MARKET_TZ),
        )
        for day in (2, 3)
    ]
    return config, bars, calendar


def test_drop_publishes_actual_cohort_and_durable_audit(tmp_path):
    config, bars, calendar = inputs(tmp_path)
    artifact = train_and_publish(
        config, config_hash(config), lambda *args: bars, lambda *args: calendar
    )
    manifest = load_manifest(artifact / "manifest.json")
    assert manifest.symbols == ["AAPL", "APC"]
    assert manifest.effective_symbols == ["AAPL"]
    report = json.loads((artifact / "report.json").read_text())
    assessment = json.loads(next((tmp_path / "_assessments").glob("*.json")).read_text())
    assert assessment["status"] == "passed_with_exclusions"
    assert assessment["outcome"] == "published"
    assert assessment["model_id"] == manifest.model_id
    assert report["rows_by_symbol"]["train"]["APC"] == 0
    assert config.symbols == ["AAPL", "APC"]


def test_strict_rejects_before_fit_with_report(tmp_path):
    config, bars, calendar = inputs(tmp_path, "strict")
    with (
        patch("carcharoth.modeling.training.train_model") as fit,
        pytest.raises(EligibilityError) as error,
    ):
        train_and_publish(config, config_hash(config), lambda *args: bars, lambda *args: calendar)
    fit.assert_not_called()
    assert error.value.path.is_file()
    assert error.value.report.status == "rejected"
    assert not list(tmp_path.glob("*/model.ubj"))


@pytest.mark.parametrize("failure", ["calendar", "fetch", "fit", "local"])
def test_failures_do_not_create_artifacts_and_preserve_cause(tmp_path, failure):
    config, bars, calendar = inputs(tmp_path)

    def fail(*args):
        raise RuntimeError("injected failure")

    with patch("carcharoth.modeling.training.train_model", side_effect=fail) as fit:
        if failure == "local":
            context = patch("carcharoth.modeling.training.persist_assessment", side_effect=fail)
        else:
            from contextlib import nullcontext

            context = nullcontext()
        with context, pytest.raises(RuntimeError, match="injected failure"):
            train_and_publish(
                config,
                config_hash(config),
                fail if failure == "fetch" else lambda *a: bars,
                fail if failure == "calendar" else lambda *a: calendar,
            )
        assert fit.call_count == (failure == "fit")
    assert not list(tmp_path.glob("*/model.ubj"))
    if failure != "local":
        report = json.loads(next((tmp_path / "_assessments").glob("*.json")).read_text())
        assert report["outcome"] == "failed"
        assert report["status"] == ("passed_with_exclusions" if failure == "fit" else "error")


def test_missing_required_benchmark_is_global_not_mass_exclusion(tmp_path):
    config, bars, calendar = inputs(tmp_path)
    from carcharoth.config.app_config import FeatureSpecConfig

    config.model.features.append(FeatureSpecConfig(name="benchmark_return", params={"period": 1}))
    with pytest.raises(EligibilityError) as error:
        train_and_publish(config, config_hash(config), lambda *args: bars, lambda *args: calendar)
    assert error.value.report.global_reasons[0].code == "unusable_benchmark:SPY"
    assert all(s.decision == "not_assessed" and not s.reasons for s in error.value.report.symbols)


def test_publication_failure_leaves_only_failed_assessment(tmp_path):
    import os
    from pathlib import Path

    config, bars, calendar = inputs(tmp_path)
    original = os.replace

    def replace(source, destination):
        if Path(destination).parent == tmp_path:
            raise OSError("model publication failed")
        return original(source, destination)

    with (
        patch("carcharoth.modeling.training.os.replace", side_effect=replace),
        pytest.raises(OSError),
    ):
        train_and_publish(config, config_hash(config), lambda *args: bars, lambda *args: calendar)
    assert not list(tmp_path.glob("*/model.ubj"))
    audit = json.loads(next((tmp_path / "_assessments").glob("*.json")).read_text())
    assert audit["outcome"] == "failed" and audit["model_id"] is None


def test_legacy_manifest_and_invalid_effective_membership(tmp_path):
    from pydantic import ValidationError

    from carcharoth.modeling.artifact import ArtifactManifest

    config, bars, calendar = inputs(tmp_path)
    artifact = train_and_publish(
        config, config_hash(config), lambda *args: bars, lambda *args: calendar
    )
    raw = json.loads((artifact / "manifest.json").read_text())
    raw.pop("effective_symbols")
    raw.pop("eligibility_assessment_id")
    assert ArtifactManifest.model_validate(raw).effective_symbols is None
    for members in ([], ["AAPL", "AAPL"], ["UNKNOWN"]):
        with pytest.raises(ValidationError, match="nonempty unique subset"):
            ArtifactManifest.model_validate({**raw, "effective_symbols": members})


def test_publisher_outage_is_nonfatal_and_future_bars_ignored(tmp_path):
    config, bars, calendar = inputs(tmp_path)
    bars["APC"] += [
        bar for bar in session("APC", 4) if bar.timestamp > datetime(2024, 1, 4, tzinfo=UTC)
    ]

    def fail(report):
        raise OSError("database unavailable")

    artifact = train_and_publish(
        config, config_hash(config), lambda *args: bars, lambda *args: calendar, fail
    )
    assert load_manifest(artifact / "manifest.json").effective_symbols == ["AAPL"]
