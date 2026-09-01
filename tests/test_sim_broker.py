from datetime import timedelta

import pytest

from carcharoth.domain.models import Bar, OrderRequest, OrderStatus, Side
from carcharoth.services.backtest.broker import SimulatedBroker
from tests.factories import BASE_TIME


def bar(open_: float, close: float, minute: int = 0) -> Bar:
    return Bar(
        "AAPL",
        BASE_TIME + timedelta(minutes=minute),
        open_,
        max(open_, close),
        min(open_, close),
        close,
        1000,
    )


def broker() -> SimulatedBroker:
    result = SimulatedBroker(100_000, spread_pct=0.001, slippage_pct=0.002)
    result.mark_to_market(BASE_TIME, {"AAPL": bar(100, 100)})
    return result


def test_order_is_accepted_then_fills_at_next_open() -> None:
    subject = broker()
    accepted = subject.submit(OrderRequest("AAPL", Side.BUY, 10))
    assert accepted.status is OrderStatus.ACCEPTED
    assert subject.get_order(accepted.broker_order_id).status is OrderStatus.ACCEPTED

    next_bar = bar(110, 115, minute=5)
    subject.mark_to_market(next_bar.timestamp, {"AAPL": next_bar})
    fill = subject.get_order(accepted.broker_order_id)
    expected = 110 * (1 + 0.001 / 2) * (1 + 0.002)
    assert fill.status is OrderStatus.FILLED
    assert fill.filled_avg_price == pytest.approx(expected)
    assert fill.filled_at == next_bar.timestamp


def test_short_position_cash_equity_and_pnl_are_signed() -> None:
    subject = broker()
    accepted = subject.submit(OrderRequest("AAPL", Side.SELL, 10))
    next_bar = bar(100, 90, minute=5)
    subject.mark_to_market(next_bar.timestamp, {"AAPL": next_bar})
    fill_price = subject.get_order(accepted.broker_order_id).filled_avg_price
    assert fill_price is not None
    state = subject.get_account_state()
    position = state.positions["AAPL"]
    assert position.qty == -10
    assert position.market_value == -900
    assert position.unrealized_pnl == pytest.approx((90 - fill_price) * -10)
    assert state.cash > 100_000
    assert state.equity == pytest.approx(state.cash - 900)


def test_buy_closes_short_on_following_bar() -> None:
    subject = broker()
    subject.submit(OrderRequest("AAPL", Side.SELL, 5))
    first = bar(100, 100, 5)
    subject.mark_to_market(first.timestamp, {"AAPL": first})
    subject.submit(OrderRequest("AAPL", Side.BUY, 5))
    second = bar(95, 95, 10)
    subject.mark_to_market(second.timestamp, {"AAPL": second})
    assert "AAPL" not in subject.get_account_state().positions
    assert subject.get_account_state().equity > 100_000


def test_pending_final_order_can_be_canceled() -> None:
    subject = broker()
    accepted = subject.submit(OrderRequest("AAPL", Side.BUY, 1))
    subject.cancel_pending()
    assert subject.get_order(accepted.broker_order_id).status is OrderStatus.CANCELED


def test_buying_power_is_margin_minus_gross_exposure() -> None:
    subject = broker()
    subject.submit(OrderRequest("AAPL", Side.BUY, 10))
    next_bar = bar(100, 100, 5)
    subject.mark_to_market(next_bar.timestamp, {"AAPL": next_bar})
    state = subject.get_account_state()
    assert state.buying_power == pytest.approx(state.equity * 2 - 1000)
