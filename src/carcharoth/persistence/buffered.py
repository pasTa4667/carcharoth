"""Write-buffered repositories for backtests.

The SQLAlchemy repositories open one transaction per call, which is right
for live trading (every event is durable immediately) but dominates backtest
runtime: a one-year run commits hundreds of thousands of single-row
transactions. These wrappers buffer the high-volume, append-only rows
(decisions, position/equity snapshots, regime evaluations) in memory and
bulk-insert them in large batches instead. They are wired only in the
backtest composition path; nothing here is used in live mode.

Only rows that are never read back during a run may be buffered — orders and
trades are queried every tick and must stay on the per-call repositories.
The owner of the ``WriteBuffer`` must call ``flush()`` after the replay loop
and before the analyzer reads the run's data.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

from carcharoth.domain.models import AccountState, ExposureIntent, RiskDecision
from carcharoth.performance import measured
from carcharoth.persistence.orm import (
    Base,
    EquitySnapshotRow,
    ModelDecisionRow,
    PositionSnapshotRow,
)
from carcharoth.persistence.repositories import (
    ModelDecisionRepository,
    PositionSnapshotRepository,
)

Row = dict[str, Any]
FlushFn = Callable[[Mapping[type[Base], list[Row]]], None]


def sqlalchemy_flush(session_factory: sessionmaker[Session]) -> FlushFn:
    """One transaction per flush, one bulk insert (executemany) per table."""

    @measured("buffer_flush")
    def flush(pending: Mapping[type[Base], list[Row]]) -> None:
        with session_factory.begin() as session:
            for row_class, rows in pending.items():
                session.execute(insert(row_class), rows)

    return flush


class WriteBuffer:
    """Accumulates row dicts per ORM class and flushes them in bulk.

    Auto-flushes once ``max_rows`` rows are pending (bounds memory on long
    runs); ``flush()`` must still be called once at the end of the run.
    Insertion order is preserved within each table.
    """

    def __init__(self, flush_fn: FlushFn, max_rows: int = 10_000) -> None:
        self._flush_fn = flush_fn
        self._max_rows = max_rows
        self._pending: dict[type[Base], list[Row]] = {}
        self._count = 0

    def add(self, row_class: type[Base], row: Row) -> None:
        self._pending.setdefault(row_class, []).append(row)
        self._count += 1
        if self._count >= self._max_rows:
            self.flush()

    def flush(self) -> None:
        if self._count == 0:
            return
        pending = self._pending
        self._pending = {}
        self._count = 0
        self._flush_fn(pending)


class BufferedModelDecisionRepository(ModelDecisionRepository):
    def __init__(self, buffer: WriteBuffer, run_id: UUID) -> None:
        self._buffer = buffer
        self._run_id = run_id

    def save(self, intent: ExposureIntent, risk: RiskDecision | None, timestamp: datetime) -> None:
        self._buffer.add(
            ModelDecisionRow,
            {
                "run_id": self._run_id,
                "timestamp": timestamp,
                "symbol": intent.symbol,
                "model_id": intent.model_id,
                "schema_hash": intent.schema_hash,
                "target": intent.target.value,
                "reason": intent.reason,
                "prediction": intent.prediction.as_dict() if intent.prediction else {},
                "features": intent.features.as_dict() if intent.features else {},
                "risk_approved": risk.approved if risk else None,
                "risk_reason": risk.reason if risk else None,
                "risk_qty": Decimal(str(risk.qty)) if risk else None,
                "order_side": risk.side.value if risk and risk.side else None,
            },
        )


class BufferedPositionSnapshotRepository(PositionSnapshotRepository):
    def __init__(self, buffer: WriteBuffer, run_id: UUID) -> None:
        self._buffer = buffer
        self._run_id = run_id

    def save_snapshot(self, timestamp: datetime, state: AccountState) -> None:
        for position in state.positions.values():
            self._buffer.add(
                PositionSnapshotRow,
                {
                    "run_id": self._run_id,
                    "timestamp": timestamp,
                    "symbol": position.symbol,
                    "qty": Decimal(str(position.qty)),
                    "avg_price": Decimal(str(position.avg_entry_price)),
                    "market_value": Decimal(str(position.market_value)),
                    "unrealized_pnl": Decimal(str(position.unrealized_pnl)),
                },
            )
        self._buffer.add(
            EquitySnapshotRow,
            {
                "run_id": self._run_id,
                "timestamp": timestamp,
                "equity": Decimal(str(state.equity)),
                "cash": Decimal(str(state.cash)),
                "buying_power": Decimal(str(state.buying_power)),
            },
        )


# Slim-mode repositories: used when --verbose-db is not set on the backtest
# command. They drop high-volume model decisions and per-position snapshots
# while keeping the equity curve,
# round trips, and computed metrics needed for Grafana and post-run analysis.


class BufferedEquityOnlyRepository(PositionSnapshotRepository):
    """Buffers only equity-curve points; skips per-symbol position rows."""

    def __init__(self, buffer: WriteBuffer, run_id: UUID) -> None:
        self._buffer = buffer
        self._run_id = run_id

    def save_snapshot(self, timestamp: datetime, state: AccountState) -> None:
        self._buffer.add(
            EquitySnapshotRow,
            {
                "run_id": self._run_id,
                "timestamp": timestamp,
                "equity": Decimal(str(state.equity)),
                "cash": Decimal(str(state.cash)),
                "buying_power": Decimal(str(state.buying_power)),
            },
        )


class NoOpModelDecisionRepository(ModelDecisionRepository):
    def save(self, intent: ExposureIntent, risk: RiskDecision | None, timestamp: datetime) -> None:
        pass
