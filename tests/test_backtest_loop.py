import json
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
import yaml

from carcharoth.backtest.loop import LoopOptions, add_months, execute_loop, plan_loop, preview
from carcharoth.config.universe_config import (
    UniverseDiscoveryConfig,
    UniverseSnapshot,
    snapshot_digest,
)
from carcharoth.domain.models import BacktestResult
from carcharoth.features.pipeline import build_feature_pipeline
from carcharoth.main import main
from carcharoth.modeling.artifact import ArtifactManifest, file_sha256


@pytest.fixture
def options(tmp_path):
    snapshot = UniverseSnapshot(
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
        discovery=UniverseDiscoveryConfig(start=date(2016, 1, 1), end=date(2016, 12, 31)),
        asset_catalogue_retrieved_at=datetime(2020, 1, 1, tzinfo=UTC),
        asset_catalogue="current",
        buckets={"all": ["AAPL", "MSFT"], "bucket-001": ["AAPL", "MSFT"]},
        scaling={},
        input_checksums={},
    )
    snapshot.content_digest = snapshot_digest(snapshot)
    path = tmp_path / "buckets.yaml"
    path.write_text(yaml.safe_dump(snapshot.model_dump(mode="json")))
    return LoopOptions(2, path, "all", train=True, train_start=date(2020, 1, 1))


def artifact(tmp_path, resolved):
    """A compatible manifest and checksummed dummy model; no XGBoost training."""
    path = tmp_path / "model"
    path.mkdir(exist_ok=True)
    (path / "model.ubj").write_bytes(b"model")
    config = resolved.config
    model = config.model
    pipeline = build_feature_pipeline(model)
    manifest = ArtifactManifest(
        model_id="test-model",
        model_sha256=file_sha256(path / "model.ubj"),
        config_hash=resolved.hash,
        schema_hash=pipeline.schema_hash,
        feature_names=list(pipeline.feature_names),
        feature_descriptors=[item.payload() for item in pipeline.descriptors],
        task=model.target.type,
        target=model.target.model_dump(mode="json"),
        timeframe_minutes=model.timeframe_minutes,
        benchmark_symbol=model.benchmark_symbol,
        symbols=config.symbols,
        universe_metadata=config.universe_metadata,
        xgboost_params=model.xgboost_params,
        seed=42,
        train_rows=10,
        validation_rows=5,
        created_at=datetime.now(UTC),
        **{
            key: getattr(config.training, key)
            for key in ("train_start", "train_end", "validation_start", "validation_end")
        },
    )
    (path / "manifest.json").write_text(manifest.model_dump_json())
    return path


def test_expanding_windows_and_owned_overrides(options):
    plans = plan_loop(options, {"model.prepared_history": True, "data.start": "2025-01-01"})
    first, second = [p.config for p in plans]
    assert first.training.train_start == second.training.train_start == date(2020, 1, 1)
    assert first.training.train_end == date(2020, 12, 31)
    assert second.training.train_end == date(2021, 3, 31)
    assert first.data.start == date(2021, 4, 1)
    assert first.data.end == date(2021, 6, 30)
    assert second.data.start == date(2021, 7, 1)
    assert second.data.end == date(2021, 9, 30)
    assert first.model.artifact_path is None
    assert not first.model.prepared_history
    assert first.symbols == ["AAPL", "MSFT"]
    assert plan_loop(replace(options, prepared_history=True))[0].config.model.prepared_history
    no_hmm = plan_loop(replace(options, hmm_enabled=False))
    assert all(not plan.config.model.hmm.enabled for plan in no_hmm)
    preview_window = preview(replace(options, hmm_enabled=False), no_hmm)["windows"][0]
    assert preview_window["hmm_enabled"] is False


@pytest.mark.parametrize("period,months", [("quarterly", 3), ("half-year", 6), ("yearly", 12)])
def test_calendar_periods(options, period, months):
    windows = replace(options, backtest_period=period).schedule(date(2030, 1, 1))
    assert windows[-1]["data.start"] == add_months(date(2020, 1, 1), 15 + months)
    assert add_months(date(2020, 2, 29), 12) == date(2021, 2, 28)
    assert add_months(date(2020, 2, 29), 48) == date(2024, 2, 29)
    end_month = replace(options, train_start=date(2020, 1, 31), train_period="quarterly")
    windows = end_month.schedule()
    assert windows[0]["training.validation_start"] == date(2020, 4, 30)
    assert windows[1]["training.validation_start"] == date(2020, 7, 31)
    assert windows[0]["data.end"].toordinal() + 1 == windows[1]["data.start"].toordinal()


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"iterations": 0}, "positive"),
        ({"iterations": -1}, "positive"),
        ({"train_start": None}, "train start"),
        ({"train_period": "monthly"}, "period"),
        ({"iterations": 1000000}, "calendar"),
        ({"train_start": date(2090, 1, 1)}, "before today"),
        ({"train": False}, "backtest start"),
        ({"train": False, "start": date(2022, 1, 1)}, "artifact"),
    ],
)
def test_invalid_schedule(options, changes, message):
    with pytest.raises(ValueError, match=message):
        replace(options, **changes).schedule()


def test_fixed_artifact_uses_manifest_dates_and_checks_compatibility(options, tmp_path):
    path = artifact(tmp_path, plan_loop(options)[0])
    fixed = replace(options, train=False, artifact=path, start=date(2021, 4, 1))
    plans = plan_loop(fixed)
    assert plans[0].config.training.train_start == date(2020, 1, 1)
    assert plans[1].config.data.start == date(2021, 7, 1)
    with pytest.raises(ValueError, match="out-of-sample"):
        plan_loop(replace(fixed, start=date(2021, 3, 31)))
    with pytest.raises(ValueError, match="configuration does not match"):
        plan_loop(fixed, {"model.xgboost_params.max_depth": 99})
    (path / "model.ubj").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash"):
        plan_loop(fixed)


def test_execution_handoff_and_failure(options, tmp_path):
    events, contexts, configs = [], [], []

    def train(resolved, context):
        assert context["iteration"] == len(events) // 2 + 1
        assert "model_id" not in context
        folder = tmp_path / str(len(events))
        folder.mkdir()
        path = artifact(folder, resolved)
        events.append(("train", path))
        return path

    def backtest(resolved, context):
        events.append(("backtest", resolved.config.model.artifact_path))
        contexts.append(context)
        configs.append(resolved.config)
        return BacktestResult(uuid4(), [])

    plans = plan_loop(options)
    results = execute_loop(options, plans, train, backtest)
    assert len(results) == 2
    assert [e[0] for e in events] == ["train", "backtest", "train", "backtest"]
    assert events[0][1] == events[1][1] != events[2][1] == events[3][1]
    assert contexts[0]["loop_id"] == contexts[1]["loop_id"]
    assert [c["iteration"] for c in contexts] == [1, 2]
    assert configs[0].backtest.initial_capital == configs[1].backtest.initial_capital
    events.clear()

    def failed_train(resolved, context):
        raise ValueError("training failed")

    with pytest.raises(ValueError, match="training failed"):
        execute_loop(options, plans, failed_train, backtest)
    assert events == []

    fixed = replace(
        options, train=False, artifact=artifact(tmp_path, plans[0]), start=date(2021, 4, 1)
    )
    execute_loop(fixed, plan_loop(fixed), failed_train, backtest)
    assert events == [("backtest", fixed.artifact)] * 2

    def failed_backtest(resolved, context):
        raise ValueError("backtest failed")

    events.clear()
    with pytest.raises(ValueError, match="backtest failed"):
        execute_loop(fixed, plan_loop(fixed), failed_train, failed_backtest)
    assert events == []


def test_invalid_universe(options):
    with pytest.raises(ValueError, match="discovery must end"):
        plan_loop(replace(options, train_start=date(2016, 1, 1)))
    with pytest.raises(Exception, match="unknown universe bucket"):
        plan_loop(replace(options, bucket="missing"))
    with pytest.raises(Exception, match="conflicts"):
        plan_loop(options, {"symbols": ["SPY"]})


def test_cli_execution_uses_stage_return_values(options, tmp_path, monkeypatch, capsys):
    stages = []

    def train(resolved, context):
        assert context["iteration"] == len(stages) // 2 + 1
        folder = tmp_path / str(len(stages))
        folder.mkdir()
        path = artifact(folder, resolved)
        stages.append(("train", path))
        return path

    def backtest(resolved, *, verbose, loop_context):
        assert not verbose
        assert loop_context["iteration"] == len(stages) // 2 + 1
        stages.append(("backtest", resolved.config.model.artifact_path))
        return BacktestResult(uuid4(), [])

    monkeypatch.setattr("carcharoth.main._run_train", train)
    monkeypatch.setattr("carcharoth.main._run_backtest", backtest)
    monkeypatch.setattr("carcharoth.logging_setup.setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr("carcharoth.main.Settings", lambda: pytest.fail("real service initialized"))
    main(
        [
            "backtest-loop",
            "--iterations",
            "2",
            "--train",
            "--train-start",
            "2020-01-01",
            "--snapshot",
            str(options.snapshot),
            "--bucket",
            "all",
        ]
    )
    assert stages[0][1] == stages[1][1] != stages[2][1] == stages[3][1]
    assert "complete (2 backtests)" in capsys.readouterr().out


@pytest.mark.parametrize("dry_run", [True, False])
def test_cli_all_buckets(options, tmp_path, monkeypatch, capsys, dry_run):
    events = []

    def train(resolved, context):
        folder = tmp_path / f"stage-{len(events)}"
        folder.mkdir()
        path = artifact(folder, resolved)
        events.append(
            (
                "train",
                resolved.config.universe.bucket,
                context["iteration"],
                context["loop_id"],
                path,
            )
        )
        return path

    def backtest(resolved, *, verbose, loop_context):
        events.append(
            (
                "backtest",
                resolved.config.universe.bucket,
                loop_context["iteration"],
                loop_context["loop_id"],
                resolved.config.model.artifact_path,
            )
        )
        return BacktestResult(uuid4(), [])

    monkeypatch.setattr("carcharoth.main._run_train", train)
    monkeypatch.setattr("carcharoth.main._run_backtest", backtest)
    monkeypatch.setattr("carcharoth.logging_setup.setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr("carcharoth.main.Settings", lambda: pytest.fail("service initialized"))
    args = [
        "backtest-loop",
        "--iterations",
        "2",
        "--train",
        "--train-start",
        "2020-01-01",
        "--snapshot",
        str(options.snapshot),
        "--all-buckets",
    ]
    main(args + (["--dry-run", "--json"] if dry_run else []))
    output = capsys.readouterr().out
    if dry_run:
        result = json.loads(output)
        assert result["buckets"] == ["all", "bucket-001"]
        assert [(w["universe"]["bucket"], w["iteration"]) for w in result["windows"]] == [
            ("all", 1),
            ("all", 2),
            ("bucket-001", 1),
            ("bucket-001", 2),
        ]
        assert [w["backtest"]["start"] for w in result["windows"]] == [
            "2021-04-01",
            "2021-07-01",
            "2021-04-01",
            "2021-07-01",
        ]
        assert events == []
    else:
        assert [(stage, bucket, iteration) for stage, bucket, iteration, _, _ in events] == [
            ("train", "all", 1),
            ("backtest", "all", 1),
            ("train", "all", 2),
            ("backtest", "all", 2),
            ("train", "bucket-001", 1),
            ("backtest", "bucket-001", 1),
            ("train", "bucket-001", 2),
            ("backtest", "bucket-001", 2),
        ]
        assert len({event[3] for event in events[:4]}) == 1
        assert len({event[3] for event in events[4:]}) == 1
        assert events[0][3] != events[4][3]
        assert all(events[i][4] == events[i + 1][4] for i in range(0, 8, 2))
        assert "universe bucket: bucket-001" in output


def test_cli_all_buckets_with_fixed_artifact(options, tmp_path, monkeypatch, capsys):
    path = artifact(tmp_path, plan_loop(options)[0])
    monkeypatch.setattr("carcharoth.main.Settings", lambda: pytest.fail("service initialized"))
    main(
        [
            "backtest-loop",
            "--iterations",
            "2",
            "--snapshot",
            str(options.snapshot),
            "--all-buckets",
            "--artifact",
            str(path),
            "--start",
            "2021-04-01",
            "--dry-run",
            "--json",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert result["train"] is False
    assert [
        (w["universe"]["bucket"], w["iteration"], w["artifact"]) for w in result["windows"]
    ] == [
        ("all", 1, str(path)),
        ("all", 2, str(path)),
        ("bucket-001", 1, str(path)),
        ("bucket-001", 2, str(path)),
    ]


def test_cli_all_buckets_preflights_every_bucket(options, monkeypatch):
    planned = []

    def reject_later_bucket(bucket_options, overrides):
        planned.append(bucket_options.bucket)
        if bucket_options.bucket == "bucket-001":
            raise ValueError("later bucket rejected")
        return plan_loop(bucket_options, overrides)

    monkeypatch.setattr("carcharoth.backtest.loop.plan_loop", reject_later_bucket)
    monkeypatch.setattr(
        "carcharoth.main._run_backtest", lambda *a, **kw: pytest.fail("replay started")
    )
    monkeypatch.setattr(
        "carcharoth.logging_setup.setup_logging", lambda *a, **kw: pytest.fail("execution started")
    )
    with pytest.raises(SystemExit, match="later bucket rejected"):
        main(
            [
                "backtest-loop",
                "--iterations",
                "1",
                "--snapshot",
                str(options.snapshot),
                "--all-buckets",
                "--train",
                "--train-start",
                "2020-01-01",
            ]
        )
    assert planned == ["all", "bucket-001"]


def test_cli_all_buckets_excludes_diagnostics_and_stops_on_failure(options, monkeypatch, capsys):
    from carcharoth.config.universe_config import load_universe_snapshot

    snapshot = load_universe_snapshot(options.snapshot)
    snapshot.buckets["bucket-001"] = ["AAPL"]
    snapshot.undersized_clusters = [["MSFT"]]
    snapshot.content_digest = snapshot_digest(snapshot)
    options.snapshot.write_text(yaml.safe_dump(snapshot.model_dump(mode="json")))
    args = [
        "backtest-loop",
        "--iterations",
        "1",
        "--snapshot",
        str(options.snapshot),
        "--all-buckets",
        "--train",
        "--train-start",
        "2020-01-01",
    ]
    main([*args, "--dry-run", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert [(w["universe"]["bucket"], w["symbol_count"]) for w in result["windows"]] == [
        ("all", 2),
        ("bucket-001", 1),
    ]
    seen = []

    def fail_train(resolved, context):
        seen.append(resolved.config.universe.bucket)
        raise ValueError("training failed")

    monkeypatch.setattr("carcharoth.main._run_train", fail_train)
    monkeypatch.setattr("carcharoth.logging_setup.setup_logging", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="training failed"):
        main(args)
    assert seen == ["all"]


def test_cli_bucket_selection_is_exclusive(options):
    args = ["backtest-loop", "--iterations", "1", "--snapshot", str(options.snapshot)]
    with pytest.raises(SystemExit) as missing:
        main(args)
    assert missing.value.code == 2
    with pytest.raises(SystemExit) as both:
        main([*args, "--bucket", "all", "--all-buckets"])
    assert both.value.code == 2


def test_local_universe_listing(options, monkeypatch, capsys):
    from carcharoth.universe import list_snapshots

    monkeypatch.setattr("carcharoth.main.Settings", lambda: pytest.fail("service initialized"))
    main(["universe", "list", "--root", str(options.snapshot.parent), "--json"])
    listing = json.loads(capsys.readouterr().out)
    assert listing["snapshots"][0]["buckets"] == {"all": 2, "bucket-001": 2}
    assert listing["snapshots"][0]["earliest_training_start"] == "2017-01-01"
    assert not listing["warnings"]
    options.snapshot.write_text("broken")
    listing = list_snapshots(options.snapshot.parent)
    assert not listing["snapshots"]
    assert "invalid universe snapshot" in listing["warnings"][0]
    assert list_snapshots(options.snapshot.parent / "missing")["snapshots"] == []


def test_dry_run_json_without_services(options, monkeypatch, capsys):
    monkeypatch.setattr("carcharoth.main.Settings", lambda: pytest.fail("service initialized"))
    main(
        [
            "backtest-loop",
            "--iterations",
            "2",
            "--train",
            "--train-start",
            "2020-01-01",
            "--snapshot",
            str(options.snapshot),
            "--bucket",
            "all",
            "--no-hmm",
            "--dry-run",
            "--json",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert result["iterations"] == 2
    assert result["windows"][1]["backtest"]["start"] == "2021-07-01"
    assert result["windows"][0]["artifact"] is None
    assert result["windows"][0]["hmm_enabled"] is False
