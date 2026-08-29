import pytest

from carcharoth.domain.models import (
    EntryCandidate,
    PositionDirection,
    Side,
    TradeIntent,
)
from tests.factories import make_position


@pytest.mark.parametrize(
    ("intent", "side", "direction", "opens", "closes"),
    [
        (TradeIntent.OPEN_LONG, Side.BUY, PositionDirection.LONG, True, False),
        (TradeIntent.CLOSE_LONG, Side.SELL, PositionDirection.LONG, False, True),
        (TradeIntent.OPEN_SHORT, Side.SELL, PositionDirection.SHORT, True, False),
        (TradeIntent.CLOSE_SHORT, Side.BUY, PositionDirection.SHORT, False, True),
        (TradeIntent.HOLD, None, None, False, False),
    ],
)
def test_trade_intent_contract(
    intent: TradeIntent,
    side: Side | None,
    direction: PositionDirection | None,
    opens: bool,
    closes: bool,
) -> None:
    assert intent.order_side is side
    assert intent.direction is direction
    assert intent.opens_position is opens
    assert intent.closes_position is closes


def test_position_direction_comes_from_signed_quantity() -> None:
    assert make_position(qty=10).direction is PositionDirection.LONG
    assert make_position(qty=-10).direction is PositionDirection.SHORT
    assert make_position(qty=-10).abs_qty == 10
    with pytest.raises(ValueError, match="zero quantity"):
        _ = make_position(qty=0).direction


def test_entry_candidate_keeps_strategy_diagnostics() -> None:
    candidate = EntryCandidate(
        direction=PositionDirection.SHORT,
        score=0.75,
        reason="bearish setup",
        indicators={"zscore": 2.1},
    )
    assert candidate.score == 0.75
    assert candidate.indicators == {"zscore": 2.1}
