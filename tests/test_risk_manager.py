from carcharoth.config.app_config import RiskConfig
from carcharoth.domain.models import Signal, SignalAction
from carcharoth.risk.basic import BasicRiskManager
from tests.factories import make_account, make_position, make_quote


def buy_signal(symbol: str = "AAPL") -> Signal:
    return Signal(symbol=symbol, action=SignalAction.BUY, strategy="test", reason="test")


def sell_signal(symbol: str = "AAPL") -> Signal:
    return Signal(symbol=symbol, action=SignalAction.SELL, strategy="test", reason="test")


def hold_signal(symbol: str = "AAPL") -> Signal:
    return Signal(symbol=symbol, action=SignalAction.HOLD, strategy="test", reason="test")


def manager(**overrides: float | int) -> BasicRiskManager:
    return BasicRiskManager(RiskConfig.model_validate(overrides))


def test_hold_is_not_actionable() -> None:
    decision = manager().assess(hold_signal(), make_account(), make_quote(100.0))
    assert not decision.approved
    assert decision.qty == 0


def test_buy_sized_by_notional_cap() -> None:
    decision = manager(max_position_notional=1000.0).assess(
        buy_signal(), make_account(equity=100_000.0), make_quote(99.99)
    )
    assert decision.approved
    assert decision.qty == 10  # floor(1000 / 100.00 ask)


def test_buy_sized_by_equity_percentage_when_smaller() -> None:
    decision = manager(max_position_notional=10_000.0, max_position_pct_equity=0.10).assess(
        buy_signal(), make_account(equity=5_000.0), make_quote(99.99)
    )
    assert decision.approved
    assert decision.qty == 5  # floor(0.10 * 5000 / 100.00)


def test_buy_rejected_when_price_exceeds_budget() -> None:
    decision = manager(max_position_notional=100.0).assess(
        buy_signal(), make_account(), make_quote(500.0)
    )
    assert not decision.approved
    assert "budget" in decision.reason


def test_buy_shrunk_to_buying_power() -> None:
    decision = manager(max_position_notional=1000.0).assess(
        buy_signal(),
        make_account(equity=100_000.0, buying_power=500.0),
        make_quote(99.99),
    )
    assert decision.approved
    assert decision.qty == 4  # floor(500 * 0.95 / (100 * 1.02))


def test_buy_rejected_without_buying_power() -> None:
    decision = manager().assess(buy_signal(), make_account(buying_power=50.0), make_quote(100.0))
    assert not decision.approved
    assert "buying power" in decision.reason


def test_buy_rejected_at_max_open_positions() -> None:
    positions = {f"SYM{i}": make_position(f"SYM{i}") for i in range(5)}
    decision = manager(max_open_positions=5).assess(
        buy_signal("AAPL"), make_account(positions=positions), make_quote(100.0)
    )
    assert not decision.approved
    assert "max open positions" in decision.reason


def test_buy_rejected_when_position_exists() -> None:
    decision = manager().assess(
        buy_signal("AAPL"),
        make_account(positions={"AAPL": make_position("AAPL")}),
        make_quote(100.0),
    )
    assert not decision.approved
    assert "pyramiding" in decision.reason


def test_buy_rejected_at_exposure_limit() -> None:
    positions = {"MSFT": make_position("MSFT", qty=100, price=499.0, market_value=49_900.0)}
    decision = manager(max_total_exposure_pct=0.50).assess(
        buy_signal("AAPL"),
        make_account(equity=100_000.0, positions=positions),
        make_quote(100.0),
    )
    assert not decision.approved
    assert "exposure" in decision.reason


def test_buy_locked_out_after_daily_loss() -> None:
    decision = manager(max_daily_loss_pct=0.03).assess(
        buy_signal(),
        make_account(equity=96_000.0, last_equity=100_000.0),
        make_quote(100.0),
    )
    assert not decision.approved
    assert "daily loss" in decision.reason


def test_sell_closes_full_position() -> None:
    decision = manager().assess(
        sell_signal("AAPL"),
        make_account(positions={"AAPL": make_position("AAPL", qty=12)}),
        make_quote(100.0),
    )
    assert decision.approved
    assert decision.qty == 12


def test_sell_without_position_rejected() -> None:
    decision = manager().assess(sell_signal("AAPL"), make_account(), make_quote(100.0))
    assert not decision.approved
    assert "no position" in decision.reason
