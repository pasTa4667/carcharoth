"""Quick-test runner: simulator fill math, sizing, independent accounting,
config validation, and end-of-run batch persistence."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from carcharoth.analysis.metrics import RoundTrip, match_round_trips
from carcharoth.config.app_config import ObjectiveConfig
from carcharoth.config.quicktest_config import QuickTestConfig, load_quicktest_config
from carcharoth.domain.models import (
    Bar,
    BarSpec,
    MetricValue,
    Position,
    Quote,
    RunType,
    Side,
    Signal,
    SignalAction,
    Timeframe,
    TimeframeUnit,
)
from carcharoth.interfaces.strategy import Strategy
from carcharoth.persistence.orm import Base, EquitySnapshotRow, TradeRow
from carcharoth.persistence.repositories import BacktestMetricsRepository, RoundTripRepository
from carcharoth.quicktest.result import QuickTestResult, SymbolResult
from carcharoth.quicktest.runner import (
    analyze_round_trips,
    compute_quicktest_metrics,
    persist_quicktest,
)
from carcharoth.quicktest.simulator import SimulationSettings, simulate_symbol
from tests.factories import make_bars
from tests.fakes import InMemoryRunRepository

# 15:00 UTC on 2026-07-01 is 11:00 New York: inside the regular session.
SESSION_TIME = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)
START = datetime(2026, 7, 1, tzinfo=UTC)
END_EXCLUSIVE = datetime(2026, 7, 2, tzinfo=UTC)

SETTINGS = SimulationSettings(capital=10_000.0, position_size_pct=0.10)


class ScriptedStrategy(Strategy):
    """Emits a scripted action keyed by the latest bar's timestamp; records
    every evaluation window for assertions."""

    name = "scripted"

    def __init__(self, actions: dict[datetime, SignalAction], lookback: int = 5) -> None:
        self._actions = actions
        self._lookback = lookback
        self.windows: list[list[Bar]] = []
        self.positions: list[Position | None] = []

    def evaluate(
        self,
        symbol: str,
        bars: list[Bar],
        quote: Quote | None,
        position: Position | None,
    ) -> Signal:
        self.windows.append(list(bars))
        self.positions.append(position)
        action = self._actions.get(bars[-1].timestamp, SignalAction.HOLD)
        return Signal(symbol=symbol, action=action, strategy=self.name, reason="scripted")

    def required_bars(self) -> BarSpec:
        return BarSpec(Timeframe(5, TimeframeUnit.MINUTE), self._lookback)


def scripted(bars: list[Bar], script: dict[int, SignalAction]) -> ScriptedStrategy:
    return ScriptedStrategy({bars[i].timestamp: action for i, action in script.items()})


def test_buy_sell_fill_math_and_cash() -> None:
    settings = SimulationSettings(
        capital=10_000.0, position_size_pct=0.10, spread_pct=0.01, slippage_pct=0.001
    )
    bars = make_bars([100.0, 100.0, 110.0, 110.0], start=SESSION_TIME)
    strategy = scripted(bars, {0: SignalAction.BUY, 2: SignalAction.SELL})

    result = simulate_symbol(strategy, "AAPL", bars, START, END_EXCLUSIVE, settings)

    buy, sell = result.trades
    buy_fill = 100.0 * (1 + 0.01 / 2) * (1 + 0.001)
    sell_fill = 110.0 * (1 - 0.01 / 2) * (1 - 0.001)
    assert buy.side is Side.BUY
    assert buy.price == pytest.approx(buy_fill)
    assert buy.qty == pytest.approx(1_000.0 / buy_fill)  # capital * pct notional
    assert sell.side is Side.SELL
    assert sell.qty == pytest.approx(buy.qty)
    assert sell.price == pytest.approx(sell_fill)
    # Final equity = cash only (flat), reflecting the round trip's pnl.
    expected_cash = 10_000.0 - buy.qty * buy_fill + buy.qty * sell_fill
    assert result.equity[-1].equity == pytest.approx(expected_cash)
    assert result.cash[-1].equity == pytest.approx(expected_cash)

    trips = match_round_trips(result.trades)
    assert len(trips) == 1
    assert trips[0].pnl == pytest.approx((sell_fill - buy_fill) * buy.qty)


def test_one_position_per_symbol_and_sell_when_flat_ignored() -> None:
    bars = make_bars([100.0] * 5, start=SESSION_TIME)
    strategy = scripted(
        bars,
        {
            0: SignalAction.SELL,  # flat: ignored
            1: SignalAction.BUY,
            2: SignalAction.BUY,  # already long: ignored
            3: SignalAction.SELL,
        },
    )
    result = simulate_symbol(strategy, "AAPL", bars, START, END_EXCLUSIVE, SETTINGS)
    assert [t.side for t in result.trades] == [Side.BUY, Side.SELL]


def test_position_and_snapshots_while_long() -> None:
    bars = make_bars([100.0, 105.0, 95.0, 100.0], start=SESSION_TIME)
    strategy = scripted(bars, {0: SignalAction.BUY, 3: SignalAction.SELL})
    result = simulate_symbol(strategy, "AAPL", bars, START, END_EXCLUSIVE, SETTINGS)

    # Strategy saw no position on the entry bar, then the open position.
    assert result.snapshots[0].timestamp == bars[0].timestamp
    assert strategy.positions[0] is None
    assert strategy.positions[1] is not None
    assert strategy.positions[1].qty == pytest.approx(10.0)
    # Bar-close unrealized P&L while long: qty 10 at entry 100.
    assert [round(s.unrealized_pnl, 6) for s in result.snapshots] == [0.0, 50.0, -50.0]


def test_window_respects_lookback_and_warmup() -> None:
    # Warm-up = bars before `start` (previous session), never evaluated.
    warmup = make_bars([90.0] * 10, start=SESSION_TIME - timedelta(days=1))
    in_window = make_bars([100.0, 101.0], start=SESSION_TIME)
    strategy = ScriptedStrategy({}, lookback=4)

    simulate_symbol(strategy, "AAPL", warmup + in_window, START, END_EXCLUSIVE, SETTINGS)

    # Warm-up bars are never evaluated but do fill the rolling window.
    assert len(strategy.windows) == 2
    assert [len(w) for w in strategy.windows] == [4, 4]
    assert strategy.windows[0][-1].timestamp == in_window[0].timestamp
    assert strategy.windows[0][0].timestamp == warmup[7].timestamp


def test_out_of_session_and_out_of_window_bars_skipped() -> None:
    pre_market = make_bars([50.0], start=datetime(2026, 7, 1, 12, 0, tzinfo=UTC))  # 08:00 ET
    after_close = make_bars([50.0], start=datetime(2026, 7, 1, 20, 30, tzinfo=UTC))  # 16:30 ET
    next_day = make_bars([50.0], start=SESSION_TIME + timedelta(days=1))
    session = make_bars([100.0, 100.0], start=SESSION_TIME)
    bars = sorted(pre_market + session + after_close + next_day, key=lambda b: b.timestamp)
    strategy = ScriptedStrategy({})

    result = simulate_symbol(strategy, "AAPL", bars, START, END_EXCLUSIVE, SETTINGS)

    assert [w[-1].timestamp for w in strategy.windows] == [b.timestamp for b in session]
    assert [p.timestamp for p in result.equity] == [b.timestamp for b in session]


def test_buy_notional_capped_by_cash() -> None:
    settings = SimulationSettings(capital=10_000.0, position_size_pct=1.0)
    bars = make_bars([100.0, 50.0, 50.0, 50.0], start=SESSION_TIME)
    # All-in at 100, sell at 50 (cash halves), buy again: notional must be
    # capped at remaining cash, not config capital.
    strategy = scripted(bars, {0: SignalAction.BUY, 1: SignalAction.SELL, 2: SignalAction.BUY})
    result = simulate_symbol(strategy, "AAPL", bars, START, END_EXCLUSIVE, settings)

    second_buy = result.trades[2]
    assert second_buy.qty * second_buy.price == pytest.approx(5_000.0)
    assert result.cash[-1].equity == pytest.approx(0.0)


def test_aggregate_equity_sums_independent_symbols() -> None:
    t0, t1, t2 = (SESSION_TIME + timedelta(minutes=5 * i) for i in range(3))
    result = QuickTestResult(capital_per_symbol=1_000.0)
    result.symbols["A"] = SymbolResult(
        symbol="A",
        equity=[p for p in make_equity([(t0, 1_000.0), (t1, 1_100.0), (t2, 1_050.0)])],
    )
    # B starts later and stops earlier: capital before its first point,
    # carry-forward after its last.
    result.symbols["B"] = SymbolResult(symbol="B", equity=make_equity([(t1, 900.0)]))

    aggregate = result.aggregate_equity()
    assert [(p.timestamp, p.equity) for p in aggregate] == [
        (t0, 2_000.0),
        (t1, 2_000.0),
        (t2, 1_950.0),
    ]


def make_equity(points: list[tuple[datetime, float]]) -> list[Any]:
    from carcharoth.domain.models import EquityPoint

    return [EquityPoint(timestamp=ts, equity=value) for ts, value in points]


def run_two_symbol_quicktest() -> QuickTestResult:
    result = QuickTestResult(capital_per_symbol=10_000.0)
    for symbol, exit_price in (("AAPL", 110.0), ("MSFT", 95.0)):
        bars = make_bars([100.0, exit_price, exit_price], symbol=symbol, start=SESSION_TIME)
        strategy = scripted(bars, {0: SignalAction.BUY, 1: SignalAction.SELL})
        result.symbols[symbol] = simulate_symbol(
            strategy, symbol, bars, START, END_EXCLUSIVE, SETTINGS
        )
    return result


def test_metrics_aggregate_fitness_and_per_symbol() -> None:
    result = run_two_symbol_quicktest()
    round_trips = analyze_round_trips(result, "scripted")
    assert all(trip.strategy == "scripted" for trip in round_trips)
    assert all(trip.mae_pct is not None and trip.mfe_pct is not None for trip in round_trips)

    objectives = {"default": ObjectiveConfig(weights={"total_return": 1.0})}
    metrics = compute_quicktest_metrics(result, round_trips, objectives)

    by_name = {m.name: m.value for m in metrics if m.symbol is None}
    assert by_name["num_trades"] == 2.0
    assert by_name["win_rate"] == pytest.approx(0.5)
    assert "fitness_default" in by_name
    assert by_name["fitness_default"] == pytest.approx(by_name["total_return"])
    # AAPL: +10 * 10 shares on 10k; MSFT: -5 * 10 shares on 10k.
    aapl = {m.name: m.value for m in metrics if m.symbol == "AAPL"}
    msft = {m.name: m.value for m in metrics if m.symbol == "MSFT"}
    assert aapl["total_return"] == pytest.approx(0.01)
    assert msft["total_return"] == pytest.approx(-0.005)
    assert aapl["win_rate"] == 1.0
    assert msft["win_rate"] == 0.0


class RecordingRoundTripRepo(RoundTripRepository):
    def __init__(self) -> None:
        self.saved: dict[UUID, list[RoundTrip]] = {}

    def save_all(self, run_id: UUID, round_trips: Sequence[RoundTrip]) -> None:
        self.saved[run_id] = list(round_trips)


class RecordingMetricsRepo(BacktestMetricsRepository):
    def __init__(self) -> None:
        self.saved: dict[UUID, list[MetricValue]] = {}

    def save_metrics(self, run_id: UUID, metrics: Sequence[MetricValue]) -> None:
        self.saved[run_id] = list(metrics)


def test_persist_quicktest_single_batch() -> None:
    result = run_two_symbol_quicktest()
    round_trips = analyze_round_trips(result, "scripted")
    metrics = compute_quicktest_metrics(result, round_trips, {})
    config = QuickTestConfig.model_validate(
        {
            "symbols": ["AAPL", "MSFT"],
            "start": START.date(),
            "end": START.date(),
            "strategy": {"name": "mean_reversion", "params": {}},
        }
    )
    runs_repo = InMemoryRunRepository()
    trips_repo = RecordingRoundTripRepo()
    metrics_repo = RecordingMetricsRepo()
    flushes: list[dict[type[Base], list[dict[str, Any]]]] = []

    run_id = persist_quicktest(
        config=config,
        result=result,
        round_trips=round_trips,
        metrics=metrics,
        started_at=datetime.now(UTC),
        runs_repo=runs_repo,
        flush=lambda pending: flushes.append(dict(pending)),
        round_trips_repo=trips_repo,
        metrics_repo=metrics_repo,
    )

    info = runs_repo.get(run_id)
    assert info is not None
    assert info.run_type is RunType.QUICKTEST
    assert info.finished_at is not None
    assert info.config["strategy"]["name"] == "mean_reversion"
    assert len(flushes) == 1  # everything in one end-of-run batch
    trade_rows = flushes[0][TradeRow]
    equity_rows = flushes[0][EquitySnapshotRow]
    assert len(trade_rows) == 4  # 2 symbols x (buy + sell)
    assert all(row["run_id"] == run_id for row in trade_rows)
    assert len(equity_rows) == 3  # union grid of both symbols (same 3 bars)
    assert trips_repo.saved[run_id] == round_trips
    assert metrics_repo.saved[run_id] == metrics


def test_config_validation(tmp_path: Path) -> None:
    yaml_text = """
symbols: [AAPL]
start: 2026-01-01
end: 2026-03-31
strategy:
  name: mean_reversion
  params: {entry_z: -1.5}
capital: 5000
position_size_pct: 0.2
"""
    path = tmp_path / "quicktest.yaml"
    path.write_text(yaml_text)
    config = load_quicktest_config(path)
    assert config.capital == 5_000.0
    assert config.strategy.params == {"entry_z": -1.5}
    assert config.spread_pct == 0.0  # frictionless by default
    assert config.end_exclusive_dt == datetime(2026, 4, 1, tzinfo=UTC)

    with pytest.raises(ValueError, match="unknown strategy"):
        QuickTestConfig.model_validate(
            {
                "symbols": ["AAPL"],
                "start": "2026-01-01",
                "end": "2026-01-31",
                "strategy": {"name": "nope"},
            }
        )
    with pytest.raises(ValueError, match="end must not be before start"):
        QuickTestConfig.model_validate(
            {
                "symbols": ["AAPL"],
                "start": "2026-02-01",
                "end": "2026-01-31",
                "strategy": {"name": "mean_reversion"},
            }
        )
    with pytest.raises(ValueError):
        QuickTestConfig.model_validate(
            {
                "symbols": ["AAPL"],
                "start": "2026-01-01",
                "end": "2026-01-31",
                "strategy": {"name": "mean_reversion"},
                "position_size_pct": 1.5,
            }
        )
