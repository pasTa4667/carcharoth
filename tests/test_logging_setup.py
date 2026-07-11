import uuid
from datetime import UTC, datetime

import yaml

from carcharoth.config.app_config import RegimeConfig, RegimeFeatureConfig, RegimeStrategyConfig, RiskConfig
from carcharoth.domain.models import MetricValue
from carcharoth.logging_setup import write_backtest_summary


def _run_id() -> uuid.UUID:
    return uuid.uuid4()


def _risk() -> RiskConfig:
    return RiskConfig()


def _regime() -> RegimeConfig:
    return RegimeConfig(
        features={"hurst": RegimeFeatureConfig(weight=1.0, params={"min_window": 8, "scale": 0.2})},
        regimes={
            "trending": RegimeStrategyConfig(strategy="ema_vwap"),
            "mean_reverting": RegimeStrategyConfig(strategy="mean_reversion"),
        },
    )


def _started_at() -> datetime:
    return datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)


def test_creates_yaml_file(tmp_path):
    run_id = _run_id()
    write_backtest_summary(tmp_path, run_id, _started_at(), _regime(), _risk(), [])
    assert (tmp_path / "backtests" / f"{run_id}.yaml").exists()


def test_creates_directory(tmp_path):
    run_id = _run_id()
    new_log_dir = tmp_path / "new_logs"
    assert not new_log_dir.exists()
    write_backtest_summary(new_log_dir, run_id, _started_at(), _regime(), _risk(), [])
    assert (new_log_dir / "backtests").exists()


def test_yaml_contains_run_id_and_date(tmp_path):
    run_id = _run_id()
    started = _started_at()
    write_backtest_summary(tmp_path, run_id, started, _regime(), _risk(), [])
    doc = yaml.safe_load((tmp_path / "backtests" / f"{run_id}.yaml").read_text())
    assert doc["run_id"] == str(run_id)
    assert doc["date"] == started.isoformat()


def test_yaml_contains_risk_config(tmp_path):
    run_id = _run_id()
    risk = RiskConfig(max_position_notional=500.0, max_open_positions=3)
    write_backtest_summary(tmp_path, run_id, _started_at(), None, risk, [])
    doc = yaml.safe_load((tmp_path / "backtests" / f"{run_id}.yaml").read_text())
    assert doc["config"]["risk"]["max_position_notional"] == 500.0
    assert doc["config"]["risk"]["max_open_positions"] == 3


def test_yaml_contains_regime_config(tmp_path):
    run_id = _run_id()
    write_backtest_summary(tmp_path, run_id, _started_at(), _regime(), _risk(), [])
    doc = yaml.safe_load((tmp_path / "backtests" / f"{run_id}.yaml").read_text())
    assert "regime" in doc["config"]
    assert doc["config"]["regime"]["lookback"] == 400


def test_no_regime_omits_key(tmp_path):
    run_id = _run_id()
    write_backtest_summary(tmp_path, run_id, _started_at(), None, _risk(), [])
    doc = yaml.safe_load((tmp_path / "backtests" / f"{run_id}.yaml").read_text())
    assert "regime" not in doc["config"]


def test_flat_metrics_in_results(tmp_path):
    run_id = _run_id()
    metrics = [
        MetricValue(name="total_return", value=0.05),
        MetricValue(name="sharpe", value=1.2),
    ]
    write_backtest_summary(tmp_path, run_id, _started_at(), None, _risk(), metrics)
    doc = yaml.safe_load((tmp_path / "backtests" / f"{run_id}.yaml").read_text())
    assert doc["results"]["total_return"] == 0.05
    assert doc["results"]["sharpe"] == 1.2


def test_per_symbol_metrics_grouped(tmp_path):
    run_id = _run_id()
    metrics = [
        MetricValue(name="symbol_pnl", value=42.0, symbol="AAPL"),
        MetricValue(name="symbol_pnl", value=-10.5, symbol="MSFT"),
    ]
    write_backtest_summary(tmp_path, run_id, _started_at(), None, _risk(), metrics)
    doc = yaml.safe_load((tmp_path / "backtests" / f"{run_id}.yaml").read_text())
    assert doc["results"]["per_symbol"]["AAPL"] == 42.0
    assert doc["results"]["per_symbol"]["MSFT"] == -10.5


def test_no_per_symbol_key_when_empty(tmp_path):
    run_id = _run_id()
    metrics = [MetricValue(name="total_return", value=0.01)]
    write_backtest_summary(tmp_path, run_id, _started_at(), None, _risk(), metrics)
    doc = yaml.safe_load((tmp_path / "backtests" / f"{run_id}.yaml").read_text())
    assert "per_symbol" not in doc["results"]
