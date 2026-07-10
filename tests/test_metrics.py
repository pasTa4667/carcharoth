"""Analyzer metrics on hand-computed synthetic data."""

from datetime import timedelta

import pytest

from carcharoth.analysis.metrics import compute_metrics, match_round_trips
from carcharoth.domain.models import EquityPoint, MetricValue, Side, TradeRecord
from tests.factories import BASE_TIME


def trade(side: Side, qty: float, price: float, minute: int, symbol: str = "AAPL") -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        timestamp=BASE_TIME + timedelta(minutes=minute),
    )


def equity_curve(values: list[float], bar_minutes: int = 5) -> list[EquityPoint]:
    return [
        EquityPoint(timestamp=BASE_TIME + timedelta(minutes=bar_minutes * i), equity=value)
        for i, value in enumerate(values)
    ]


def by_name(metrics: list[MetricValue]) -> dict[str, float]:
    return {m.name: m.value for m in metrics if m.symbol is None}


def test_fifo_matching_with_partial_lots() -> None:
    trips = match_round_trips(
        [
            trade(Side.BUY, 10, 100.0, minute=0),
            trade(Side.BUY, 5, 110.0, minute=5),
            trade(Side.SELL, 12, 120.0, minute=10),
        ]
    )

    assert len(trips) == 2
    first, second = trips
    assert (first.qty, first.entry_price, first.pnl) == (10, 100.0, pytest.approx(200.0))
    assert (second.qty, second.entry_price, second.pnl) == (2, 110.0, pytest.approx(20.0))
    assert first.opened_at == BASE_TIME
    assert first.closed_at == BASE_TIME + timedelta(minutes=10)


def test_sell_without_matching_lot_is_skipped() -> None:
    assert match_round_trips([trade(Side.SELL, 5, 100.0, minute=0)]) == []


def test_round_trips_are_matched_per_symbol() -> None:
    trips = match_round_trips(
        [
            trade(Side.BUY, 1, 100.0, minute=0, symbol="AAPL"),
            trade(Side.BUY, 1, 200.0, minute=1, symbol="MSFT"),
            trade(Side.SELL, 1, 210.0, minute=2, symbol="MSFT"),
        ]
    )
    assert [(t.symbol, t.pnl) for t in trips] == [("MSFT", pytest.approx(10.0))]


def test_equity_metrics_total_return_and_max_drawdown() -> None:
    metrics = by_name(compute_metrics(equity_curve([100.0, 110.0, 99.0, 108.0]), []))

    assert metrics["total_return"] == pytest.approx(0.08)
    assert metrics["max_drawdown"] == pytest.approx(1 - 99.0 / 110.0)


def test_sharpe_zero_variance_is_zero_and_annualization_scales() -> None:
    flat_growth = equity_curve([100.0, 101.0, 102.01])  # constant +1% per bar
    assert by_name(compute_metrics(flat_growth, []))["sharpe"] == pytest.approx(0.0)

    varied = equity_curve([100.0, 102.0, 101.0, 104.0, 103.0])
    five_minute = by_name(compute_metrics(varied, []))["sharpe"]
    daily = by_name(
        compute_metrics(equity_curve([100.0, 102.0, 101.0, 104.0, 103.0], 24 * 60), [])
    )["sharpe"]
    # same returns, coarser bars -> fewer periods per year -> smaller Sharpe
    assert abs(daily) < abs(five_minute)


def test_trade_metrics_win_rate_profit_factor_and_averages() -> None:
    trades = [
        trade(Side.BUY, 10, 100.0, minute=0),  # +100
        trade(Side.SELL, 10, 110.0, minute=5),
        trade(Side.BUY, 10, 100.0, minute=10),  # -50
        trade(Side.SELL, 10, 95.0, minute=15),
        trade(Side.BUY, 10, 100.0, minute=20),  # +200
        trade(Side.SELL, 10, 120.0, minute=25),
    ]
    metrics = by_name(compute_metrics([], trades))

    assert metrics["num_trades"] == 3
    assert metrics["win_rate"] == pytest.approx(2 / 3)
    assert metrics["avg_win"] == pytest.approx(150.0)
    assert metrics["avg_loss"] == pytest.approx(-50.0)
    assert metrics["profit_factor"] == pytest.approx(300.0 / 50.0)


def test_symbol_pnl_breakdown() -> None:
    trades = [
        trade(Side.BUY, 10, 100.0, minute=0, symbol="AAPL"),
        trade(Side.SELL, 10, 110.0, minute=5, symbol="AAPL"),
        trade(Side.BUY, 5, 200.0, minute=0, symbol="MSFT"),
        trade(Side.SELL, 5, 190.0, minute=5, symbol="MSFT"),
    ]
    per_symbol = {m.symbol: m.value for m in compute_metrics([], trades) if m.name == "symbol_pnl"}
    assert per_symbol == {"AAPL": pytest.approx(100.0), "MSFT": pytest.approx(-50.0)}


def test_empty_inputs_yield_only_num_trades() -> None:
    metrics = compute_metrics([], [])
    assert [(m.name, m.value) for m in metrics] == [("num_trades", 0.0)]


def test_all_winning_trades_omit_loss_metrics() -> None:
    trades = [
        trade(Side.BUY, 10, 100.0, minute=0),
        trade(Side.SELL, 10, 110.0, minute=5),
    ]
    metrics = by_name(compute_metrics([], trades))
    assert metrics["win_rate"] == 1.0
    assert "avg_loss" not in metrics
    assert "profit_factor" not in metrics
