"""SimulatedBroker: fill math, position accounting and account state."""

from datetime import UTC, datetime, timedelta

import pytest

from carcharoth.domain.errors import BrokerError
from carcharoth.domain.models import OrderRequest, OrderStatus, Side
from carcharoth.services.backtest.broker import SimulatedBroker
from tests.factories import BASE_TIME

SPREAD = 0.001
SLIPPAGE = 0.002


def make_broker(
    capital: float = 100_000.0, spread: float = SPREAD, slippage: float = SLIPPAGE
) -> SimulatedBroker:
    broker = SimulatedBroker(initial_capital=capital, spread_pct=spread, slippage_pct=slippage)
    broker.mark_to_market(BASE_TIME, {"AAPL": 100.0, "MSFT": 200.0})
    return broker


def buy(broker: SimulatedBroker, symbol: str = "AAPL", qty: float = 10) -> str:
    result = broker.submit(OrderRequest(symbol=symbol, side=Side.BUY, qty=qty))
    return result.broker_order_id


def test_buy_fills_at_ask_plus_slippage_and_debits_cash() -> None:
    broker = make_broker()
    order_id = buy(broker, qty=10)

    fill = broker.get_order(order_id)
    expected_price = 100.0 * (1 + SPREAD / 2) * (1 + SLIPPAGE)
    assert fill.filled_avg_price == pytest.approx(expected_price)
    assert broker.get_account_state().cash == pytest.approx(100_000.0 - 10 * expected_price)


def test_sell_fills_at_bid_minus_slippage_and_credits_cash() -> None:
    broker = make_broker()
    buy(broker, qty=10)
    cash_before = broker.get_account_state().cash

    result = broker.submit(OrderRequest(symbol="AAPL", side=Side.SELL, qty=10))
    fill = broker.get_order(result.broker_order_id)

    expected_price = 100.0 * (1 - SPREAD / 2) * (1 - SLIPPAGE)
    assert fill.filled_avg_price == pytest.approx(expected_price)
    assert broker.get_account_state().cash == pytest.approx(cash_before + 10 * expected_price)
    assert "AAPL" not in broker.get_account_state().positions


def test_submit_returns_accepted_then_get_order_reports_filled() -> None:
    broker = make_broker()
    result = broker.submit(OrderRequest(symbol="AAPL", side=Side.BUY, qty=5))

    assert result.status is OrderStatus.ACCEPTED
    assert result.filled_qty == 0
    assert result.filled_avg_price is None
    assert result.submitted_at == BASE_TIME
    assert result.filled_at is None

    fill = broker.get_order(result.broker_order_id)
    assert fill.status is OrderStatus.FILLED
    assert fill.filled_qty == 5
    assert fill.filled_at == BASE_TIME


def test_buy_adds_compute_weighted_average_entry() -> None:
    broker = make_broker(spread=0.0, slippage=0.0)
    buy(broker, qty=10)  # @100
    broker.mark_to_market(BASE_TIME + timedelta(minutes=5), {"AAPL": 110.0})
    buy(broker, qty=10)  # @110

    position = broker.get_account_state().positions["AAPL"]
    assert position.qty == 20
    assert position.avg_entry_price == pytest.approx(105.0)


def test_oversell_and_overspend_raise() -> None:
    broker = make_broker(capital=500.0, spread=0.0, slippage=0.0)
    with pytest.raises(BrokerError, match="cannot sell"):
        broker.submit(OrderRequest(symbol="AAPL", side=Side.SELL, qty=1))
    with pytest.raises(BrokerError, match="insufficient cash"):
        broker.submit(OrderRequest(symbol="AAPL", side=Side.BUY, qty=10))  # 1000 > 500


def test_submit_without_price_raises() -> None:
    broker = make_broker()
    with pytest.raises(BrokerError, match="no price"):
        broker.submit(OrderRequest(symbol="UNKNOWN", side=Side.BUY, qty=1))


def test_mark_to_market_updates_position_and_equity() -> None:
    broker = make_broker(spread=0.0, slippage=0.0)
    buy(broker, qty=10)  # @100
    broker.mark_to_market(BASE_TIME + timedelta(minutes=5), {"AAPL": 105.0})

    state = broker.get_account_state()
    position = state.positions["AAPL"]
    assert position.current_price == 105.0
    assert position.market_value == pytest.approx(1050.0)
    assert position.unrealized_pnl == pytest.approx(50.0)
    assert state.equity == pytest.approx(state.cash + 1050.0)
    assert state.buying_power == state.cash


def test_last_equity_rolls_at_session_change() -> None:
    broker = make_broker(spread=0.0, slippage=0.0)
    buy(broker, qty=10)  # @100
    assert broker.get_account_state().last_equity == pytest.approx(100_000.0)

    # same session: last_equity unchanged even though price moved
    broker.mark_to_market(BASE_TIME + timedelta(minutes=30), {"AAPL": 110.0})
    assert broker.get_account_state().last_equity == pytest.approx(100_000.0)

    # next session: rolls to the previous session's closing equity (price 110)
    broker.mark_to_market(BASE_TIME + timedelta(days=1), {"AAPL": 120.0})
    assert broker.get_account_state().last_equity == pytest.approx(100_000.0 + 10 * 10.0)


def test_cancel_order_is_a_tolerant_noop() -> None:
    broker = make_broker()
    broker.cancel_order("nonexistent")  # must not raise


def test_get_order_unknown_id_raises() -> None:
    broker = make_broker()
    with pytest.raises(BrokerError, match="unknown order"):
        broker.get_order("nope")


def test_submit_before_mark_to_market_raises() -> None:
    broker = SimulatedBroker(initial_capital=1000.0, spread_pct=0.0, slippage_pct=0.0)
    with pytest.raises(BrokerError, match="clock not set"):
        broker.submit(OrderRequest(symbol="AAPL", side=Side.BUY, qty=1))


def test_timestamps_are_simulated_not_wall_clock() -> None:
    historical = datetime(2020, 3, 2, 15, 0, tzinfo=UTC)
    broker = SimulatedBroker(initial_capital=1000.0, spread_pct=0.0, slippage_pct=0.0)
    broker.mark_to_market(historical, {"AAPL": 10.0})
    result = broker.submit(OrderRequest(symbol="AAPL", side=Side.BUY, qty=1))
    fill = broker.get_order(result.broker_order_id)
    assert result.submitted_at == historical
    assert fill.filled_at == historical
