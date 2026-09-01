from carcharoth.config.app_config import RiskConfig
from carcharoth.domain.models import ExposureIntent, Side, TargetExposure
from carcharoth.risk.basic import BasicRiskManager
from tests.factories import make_account, make_position, make_quote


def intent(target: TargetExposure, symbol: str = "AAPL") -> ExposureIntent:
    return ExposureIntent(symbol, target, "model", "schema", "test")


def manager(**overrides: float | int) -> BasicRiskManager:
    return BasicRiskManager(RiskConfig.model_validate(overrides))


def test_flat_to_long_and_short_are_sized_symmetrically() -> None:
    account = make_account(equity=100_000, buying_power=100_000)
    long = manager(max_position_notional=1000).assess(
        intent(TargetExposure.LONG), account, make_quote(100)
    )
    short = manager(max_position_notional=1000).assess(
        intent(TargetExposure.SHORT), account, make_quote(100)
    )
    assert long.approved and long.side is Side.BUY and long.qty == 9
    assert short.approved and short.side is Side.SELL and short.qty == 10


def test_long_and_short_close_exact_signed_quantity() -> None:
    long = manager().assess(
        intent(TargetExposure.FLAT),
        make_account(positions={"AAPL": make_position(qty=12)}),
        make_quote(100),
    )
    short_position = make_position(qty=-7, market_value=-700)
    short = manager().assess(
        intent(TargetExposure.FLAT),
        make_account(positions={"AAPL": short_position}),
        make_quote(100),
    )
    assert (long.side, long.qty) == (Side.SELL, 12)
    assert (short.side, short.qty) == (Side.BUY, 7)


def test_reversal_closes_first() -> None:
    decision = manager().assess(
        intent(TargetExposure.SHORT),
        make_account(positions={"AAPL": make_position(qty=5)}),
        make_quote(100),
    )
    assert decision.approved
    assert decision.side is Side.SELL
    assert decision.qty == 5
    assert "reversal" in decision.reason


def test_closes_are_allowed_during_daily_loss_lockout() -> None:
    decision = manager(max_daily_loss_pct=0.03).assess(
        intent(TargetExposure.FLAT),
        make_account(
            equity=90_000,
            last_equity=100_000,
            positions={"AAPL": make_position(qty=-5, market_value=-500)},
        ),
        make_quote(100),
    )
    assert decision.approved


def test_gross_exposure_uses_absolute_short_value() -> None:
    account = make_account(
        equity=10_000,
        positions={"MSFT": make_position("MSFT", qty=-40, price=100, market_value=-4000)},
    )
    decision = manager(
        max_total_exposure_pct=0.40,
        max_position_notional=1000,
    ).assess(intent(TargetExposure.LONG), account, make_quote(100))
    assert not decision.approved
    assert "gross exposure" in decision.reason


def test_same_target_does_not_pyramid() -> None:
    decision = manager().assess(
        intent(TargetExposure.LONG),
        make_account(positions={"AAPL": make_position(qty=5)}),
        make_quote(100),
    )
    assert not decision.approved
    assert decision.side is None
