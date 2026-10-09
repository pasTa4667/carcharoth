from contextlib import nullcontext
from dataclasses import asdict
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest

from carcharoth.backtest.runner import BacktestRunner
from carcharoth.config.app_config import RiskConfig
from carcharoth.config.loader import load_profile
from carcharoth.domain.models import Bar, ExposureIntent, TargetExposure
from carcharoth.engine.engine import TradingEngine
from carcharoth.main import _build_parser, _parse_performance_interval, run_backtest_once
from carcharoth.performance import Monitor, stage
from carcharoth.risk.basic import BasicRiskManager
from carcharoth.services.backtest.broker import SimulatedBroker
from carcharoth.services.backtest.market_data import HistoricalMarketDataService
from tests.factories import BASE_TIME
from tests.fakes import (
    FakeModelSignalGenerator,
    InMemoryModelDecisionRepository,
    InMemoryOrderRepository,
    InMemoryPositionSnapshotRepository,
    InMemoryTradeRepository,
)


def replay(
    monitored: bool,
    broken: bool = False,
    prepared: bool | None = None,
    prediction: float = 0.01,
) -> tuple[Any, Monitor]:
    bars = [
        Bar("AAPL", BASE_TIME + timedelta(minutes=5 * i), price, price, price, price, 1000)
        for i, price in enumerate([100.0, 110.0, 120.0])
    ]
    market = HistoricalMarketDataService(
        {"AAPL": bars, "SPY": bars}, spread_pct=0.0, tradable_symbols=["AAPL"]
    )
    broker = SimulatedBroker(100_000, 0.0, 0.0)
    orders = InMemoryOrderRepository()
    trades = InMemoryTradeRepository()
    decisions = InMemoryModelDecisionRepository()
    snapshots = InMemoryPositionSnapshotRepository()
    model = FakeModelSignalGenerator(
        {"AAPL": ExposureIntent("AAPL", TargetExposure.LONG, "model", "schema", "long")},
        lookback=1,
    )
    if prepared is not None:
        from tests.test_model_signal_generator import generator

        model, _ = generator(prediction)
        if prepared:
            model.prepare_history(
                {"AAPL": bars, "SPY": bars},
                symbols=["AAPL"],
                start=bars[0].timestamp,
                end_exclusive=bars[-1].timestamp + timedelta(minutes=5),
            )
    engine = TradingEngine(
        market,
        broker,
        model,
        BasicRiskManager(RiskConfig(max_position_notional=100.0 if prepared is None else 1000.0)),
        broker,
        decisions,
        orders,
        trades,
        snapshots,
        ["AAPL"],
    )
    runner = BacktestRunner(
        engine,
        market,
        broker,
        orders,
        trades,
        broker,
        bars[0].timestamp,
        bars[-1].timestamp,
    )
    monitor = Monitor()
    broken_model = patch.object(model, "generate", side_effect=ValueError("symbol failure"))
    with (
        monitor.execution() if monitored else nullcontext(),
        broken_model if broken else nullcontext(),
        stage("simulation"),
    ):
        runner.run()
    fills = [
        {
            key: value
            for key, value in asdict(fill).items()
            if key not in {"broker_order_id", "client_order_id"}
        }
        for fill in trades.fills
    ]
    return (fills, decisions.saved, snapshots.snapshots, broker.get_account_state()), monitor


def test_replay_parity_and_counters() -> None:
    baseline, _ = replay(False)
    monitored, monitor = replay(True)
    assert monitored == baseline
    assert monitor.counters == {"total_ticks": 3, "symbol_attempts": 3, "ticks": 3}
    assert any(s.path == "simulation/reconcile" for s in monitor.snapshot().stages)


@pytest.mark.parametrize("prediction", [0.01, -0.01])
def test_full_engine_prepared_legacy_parity_on_compatible_features(prediction) -> None:
    legacy, legacy_monitor = replay(True, prepared=False, prediction=prediction)
    prepared, prepared_monitor = replay(True, prepared=True, prediction=prediction)
    assert prepared == legacy  # intents/risk, fills, positions/equity, account
    assert prepared_monitor.counters["predictions"] == legacy_monitor.counters["predictions"]
    assert prepared_monitor.counters.get("feature_matrices", 0) == 0  # none during replay
    assert legacy_monitor.counters["feature_matrices"] == 3
    assert prepared[0]  # an actual next-open fill, not vacuous no-trade parity


def test_swallowed_symbol_error_preserved_and_counted() -> None:
    baseline, _ = replay(False, broken=True)
    monitored, monitor = replay(True, broken=True)
    assert monitored == baseline
    assert monitor.counters["symbol_errors"] == 3
    assert monitor.snapshot().status == "SUCCEEDED"


def test_setup_failure_finalizes_before_run_id_exists() -> None:
    monitor = Monitor()
    config = load_profile("backtest").config
    with (
        patch("carcharoth.modeling.build_signal_generator", side_effect=ValueError("bad artifact")),
        pytest.raises(ValueError, match="bad artifact"),
    ):
        run_backtest_once(
            config,
            config.data.start_dt,
            config.data.end_exclusive_dt,
            config.symbols,
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            performance_monitor=monitor,
        )
    snap = monitor.snapshot()
    assert snap.status == "FAILED"
    assert snap.run_id is None
    assert snap.stages[0].path == "setup"


def test_cli_interval_validation_and_options() -> None:
    for value in ["-1", "nan", "inf", "bad"]:
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["backtest", "--performance-interval", value])
    assert _parse_performance_interval("0") == 0
    args = _build_parser().parse_args(["backtest", "--profile-output", "logs/test.pstats"])
    assert str(args.profile_output) == "logs/test.pstats"


def test_cache_cleanup_is_version_scoped(capsys) -> None:
    from carcharoth.main import _run_cache_clear, _run_cache_stats
    from tests.fakes import InMemoryByteStore

    store = InMemoryByteStore()
    store.data.update({"carch:bars:v1:old": b"old", "carch:bars:v2:month": b"new"})
    args = _build_parser().parse_args(["cache", "clear"])
    assert args.bars_version == "v2"
    with patch("carcharoth.main._cache_store_or_exit", return_value=store):
        _run_cache_stats()
        assert "server-wide" in capsys.readouterr().out
        _run_cache_clear()
        assert store.data == {"carch:bars:v1:old": b"old"}
        _run_cache_clear("v1")
        assert not store.data
