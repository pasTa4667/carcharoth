"""Read-only CLI requests must work without importing execution dependencies."""

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from carcharoth.config.loader import load_profile
from carcharoth.config.universe_config import (
    UniverseDiscoveryConfig,
    UniverseSnapshot,
    snapshot_digest,
)
from carcharoth.universe import list_snapshots

REPO = Path(__file__).resolve().parents[1]
# Block imports, rather than assert timings: this also catches regressions when
# pytest's own process has already imported the scientific/trading stack.
ISOLATED_CLI = """
import importlib.abc
import sys

class NoExecutionImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = (
            'numpy', 'pandas', 'scipy', 'sklearn', 'hmmlearn', 'xgboost',
            'sqlalchemy', 'optuna', 'alpaca', 'redis',
            'carcharoth.analysis', 'carcharoth.backtest', 'carcharoth.engine',
            'carcharoth.features', 'carcharoth.modeling', 'carcharoth.persistence',
            'carcharoth.services', 'carcharoth.logging_setup',
        )
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise AssertionError('read-only CLI imported ' + fullname)

sys.meta_path.insert(0, NoExecutionImports())
from carcharoth.main import main
main(sys.argv[1:])
"""


@pytest.fixture
def workspace(tmp_path):
    shutil.copytree(REPO / "config", tmp_path / "config")
    return tmp_path


def run_cli(workspace, *args):
    result = subprocess.run(
        [sys.executable, "-B", "-c", ISOLATED_CLI, *args],
        cwd=workspace,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert "read-only CLI imported" not in result.stderr, result.stderr
    assert not (workspace / "logs").exists()
    return result


@pytest.mark.parametrize("profile", ["backtest", "trading/paper"])
@pytest.mark.parametrize("overrides", [{}, {"risk.max_open_positions": 12}])
def test_config_json_and_hash_parity(workspace, profile, overrides):
    expected = load_profile(profile, overrides, config_dir=workspace / "config")
    args = ["-p", profile]
    for key, value in overrides.items():
        args.extend(["--set", f"{key}={value}"])
    result = run_cli(workspace, "config", "resolve", *args, "--format", "json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "config_hash": expected.hash,
        "config": expected.config.model_dump(mode="json"),
    }
    validated = run_cli(workspace, "config", "validate", *args, "--json")
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["config_hash"] == expected.hash
    hashed = run_cli(workspace, "config", "hash", *args)
    assert hashed.returncode == 0, hashed.stderr
    assert hashed.stdout.strip() == expected.hash


@pytest.mark.parametrize(
    "args,error_path",
    [
        (["-p", "missing-profile"], ""),
        (["-p", "backtest", "--set", "risk.max_position_pct_equity=1.5"],
         "risk.max_position_pct_equity"),
        (["-p", "backtest", "--set", "unknown.path=1"], ""),
    ],
)
def test_validation_failures_stay_structured(workspace, args, error_path):
    result = run_cli(workspace, "config", "validate", *args, "--json")
    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    assert any(error["path"] == error_path for error in payload["errors"])
    assert result.stderr == ""


def test_catalogue_parity_without_discovery_imports(workspace):
    root = workspace / "universes"
    root.mkdir()
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
    valid = root / "valid"
    valid.mkdir()
    (valid / "buckets.yaml").write_text(yaml.safe_dump(snapshot.model_dump(mode="json")))
    broken = root / "broken"
    broken.mkdir()
    (broken / "buckets.yaml").write_text("broken")
    for path in (root, root / "missing", workspace):
        result = run_cli(workspace, "universe", "list", "--root", str(path), "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == list_snapshots(path)
