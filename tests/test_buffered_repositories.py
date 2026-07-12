"""Tests for the write-buffered backtest repositories.

The flush function is a recording fake; row-dict contents are asserted to
match exactly what the SQLAlchemy repositories would insert.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from carcharoth.domain.models import RiskDecision, Signal, SignalAction
from carcharoth.persistence.buffered import (
    BufferedPositionSnapshotRepository,
    BufferedRegimeEvaluationRepository,
    BufferedStrategyDecisionRepository,
    Row,
    WriteBuffer,
)
from carcharoth.persistence.orm import (
    Base,
    EquitySnapshotRow,
    PositionSnapshotRow,
    RegimeEvaluationRow,
    StrategyDecisionRow,
)
from carcharoth.regime.models import Evidence, Regime, RegimeAssessment
from tests.factories import make_account, make_position

AS_OF = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)
RUN_ID = uuid4()


class RecordingFlush:
    def __init__(self) -> None:
        self.flushes: list[dict[type[Base], list[Row]]] = []

    def __call__(self, pending: Mapping[type[Base], list[Row]]) -> None:
        self.flushes.append({row_class: list(rows) for row_class, rows in pending.items()})


def test_buffer_holds_rows_until_flush() -> None:
    flush = RecordingFlush()
    buffer = WriteBuffer(flush)
    buffer.add(StrategyDecisionRow, {"symbol": "AAPL"})
    assert flush.flushes == []

    buffer.flush()
    assert flush.flushes == [{StrategyDecisionRow: [{"symbol": "AAPL"}]}]


def test_buffer_flush_is_noop_when_empty() -> None:
    flush = RecordingFlush()
    WriteBuffer(flush).flush()
    assert flush.flushes == []


def test_buffer_auto_flushes_at_max_rows() -> None:
    flush = RecordingFlush()
    buffer = WriteBuffer(flush, max_rows=3)
    for i in range(7):
        buffer.add(StrategyDecisionRow, {"n": i})

    assert [len(rows[StrategyDecisionRow]) for rows in flush.flushes] == [3, 3]
    buffer.flush()
    assert [row["n"] for batch in flush.flushes for row in batch[StrategyDecisionRow]] == list(
        range(7)
    )


def test_buffer_groups_by_table_and_preserves_order() -> None:
    flush = RecordingFlush()
    buffer = WriteBuffer(flush)
    buffer.add(EquitySnapshotRow, {"n": 1})
    buffer.add(PositionSnapshotRow, {"n": 2})
    buffer.add(EquitySnapshotRow, {"n": 3})
    buffer.flush()

    assert flush.flushes == [
        {
            EquitySnapshotRow: [{"n": 1}, {"n": 3}],
            PositionSnapshotRow: [{"n": 2}],
        }
    ]


def test_decision_repository_buffers_full_row() -> None:
    flush = RecordingFlush()
    buffer = WriteBuffer(flush)
    repo = BufferedStrategyDecisionRepository(buffer, RUN_ID)
    signal = Signal(
        symbol="AAPL",
        action=SignalAction.BUY,
        strategy="mean_reversion",
        reason="z below entry",
        indicators={"zscore": -2.5},
    )
    risk = RiskDecision(signal=signal, approved=True, qty=3.0, reason="ok")

    repo.save(signal, risk, AS_OF)
    buffer.flush()

    assert flush.flushes == [
        {
            StrategyDecisionRow: [
                {
                    "run_id": RUN_ID,
                    "timestamp": AS_OF,
                    "symbol": "AAPL",
                    "strategy": "mean_reversion",
                    "signal": "buy",
                    "reason": "z below entry",
                    "indicators": {"zscore": -2.5},
                    "risk_approved": True,
                    "risk_reason": "ok",
                    "risk_qty": Decimal("3.0"),
                }
            ]
        }
    ]


def test_decision_repository_handles_missing_risk() -> None:
    flush = RecordingFlush()
    buffer = WriteBuffer(flush)
    repo = BufferedStrategyDecisionRepository(buffer, RUN_ID)
    signal = Signal(symbol="AAPL", action=SignalAction.HOLD, strategy="fake", reason="")

    repo.save(signal, None, AS_OF)
    buffer.flush()

    (row,) = flush.flushes[0][StrategyDecisionRow]
    assert row["signal"] == "hold"
    assert row["risk_approved"] is None
    assert row["risk_reason"] is None
    assert row["risk_qty"] is None


def test_snapshot_repository_buffers_positions_and_equity() -> None:
    flush = RecordingFlush()
    buffer = WriteBuffer(flush)
    repo = BufferedPositionSnapshotRepository(buffer, RUN_ID)
    state = make_account(
        equity=100_000.0,
        cash=99_000.0,
        buying_power=95_000.0,
        positions={"AAPL": make_position("AAPL", qty=10, price=100.0)},
    )

    repo.save_snapshot(AS_OF, state)
    buffer.flush()

    (batch,) = flush.flushes
    assert batch[PositionSnapshotRow] == [
        {
            "run_id": RUN_ID,
            "timestamp": AS_OF,
            "symbol": "AAPL",
            "qty": Decimal("10"),
            "avg_price": Decimal("100.0"),
            "market_value": Decimal("1000.0"),
            "unrealized_pnl": Decimal("0.0"),
        }
    ]
    assert batch[EquitySnapshotRow] == [
        {
            "run_id": RUN_ID,
            "timestamp": AS_OF,
            "equity": Decimal("100000.0"),
            "cash": Decimal("99000.0"),
            "buying_power": Decimal("95000.0"),
        }
    ]


def test_regime_repository_buffers_features_with_weights() -> None:
    flush = RecordingFlush()
    buffer = WriteBuffer(flush)
    repo = BufferedRegimeEvaluationRepository(buffer, RUN_ID)
    assessment = RegimeAssessment(
        symbol="AAPL",
        regime=Regime.TRENDING,
        score=0.42,
        directional_score=0.6,
        stability=0.7,
        evidence=(Evidence(feature="hurst", value=0.61, direction=0.8, stability=None),),
    )

    repo.save(assessment, {"hurst": 1.0}, AS_OF)
    buffer.flush()

    assert flush.flushes == [
        {
            RegimeEvaluationRow: [
                {
                    "run_id": RUN_ID,
                    "timestamp": AS_OF,
                    "symbol": "AAPL",
                    "regime": "trending",
                    "score": 0.42,
                    "directional_score": 0.6,
                    "stability": 0.7,
                    "features": {
                        "hurst": {
                            "value": 0.61,
                            "direction": 0.8,
                            "stability": None,
                            "weight": 1.0,
                        }
                    },
                }
            ]
        }
    ]
