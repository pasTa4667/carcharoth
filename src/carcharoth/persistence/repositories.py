"""Repository interfaces and their SQLAlchemy implementations.

Each repository opens a short transaction per call; nothing holds a session
across engine steps. Tests use in-memory fakes of the ABCs instead.

Data repositories are scoped to one run: they are constructed with a run_id
and both writes and reads only touch that run's rows. This isolates
backtests from live data (and from each other). Consequences: a restarted
live process does not reconcile the previous run's open orders (DAY market
orders fill within seconds, so this is negligible) and regime assignments
start fresh each run.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from carcharoth.analysis.metrics import RoundTrip
from carcharoth.domain.models import (
    TERMINAL_ORDER_STATUSES,
    AccountState,
    AssignmentRecord,
    DecisionRecord,
    EquityPoint,
    MetricValue,
    OpenOrder,
    OrderRequest,
    OrderResult,
    RiskDecision,
    RunInfo,
    RunType,
    Side,
    Signal,
    TradeRecord,
)
from carcharoth.persistence.orm import (
    BacktestMetricRow,
    ConfigurationRow,
    EquitySnapshotRow,
    OrderRow,
    PositionSnapshotRow,
    RegimeEvaluationRow,
    RoundTripRow,
    RunRow,
    StrategyAssignmentRow,
    StrategyDecisionRow,
    TradeRow,
)
from carcharoth.regime.models import Regime, RegimeAssessment, StrategyAssignment


def evidence_payload(assessment: RegimeAssessment) -> dict[str, Any]:
    """The JSONB `features` payload of a regime evaluation row."""
    return {
        e.feature: {
            "value": e.value,
            "direction": e.direction,
            "stability": e.stability,
            "weight": e.weight,
        }
        for e in assessment.evidence
    }


def probabilities_payload(assessment: RegimeAssessment) -> dict[str, float] | None:
    """The JSONB `probabilities` payload; None for non-probabilistic detectors."""
    if assessment.probabilities is None:
        return None
    return {regime.value: prob for regime, prob in assessment.probabilities.items()}


class StrategyDecisionRepository(ABC):
    @abstractmethod
    def save(self, signal: Signal, risk: RiskDecision | None, timestamp: datetime) -> None: ...


class OrderRepository(ABC):
    @abstractmethod
    def save_submitted(self, request: OrderRequest, result: OrderResult) -> None: ...

    @abstractmethod
    def update_from_broker(self, result: OrderResult) -> None: ...

    @abstractmethod
    def find_open_broker_order_ids(self) -> list[str]: ...

    @abstractmethod
    def find_open_orders(self, symbol: str) -> list[OpenOrder]: ...


class TradeRepository(ABC):
    @abstractmethod
    def save_fill(self, result: OrderResult) -> None: ...

    @abstractmethod
    def exists_for_order(self, broker_order_id: str) -> bool: ...


class PositionSnapshotRepository(ABC):
    @abstractmethod
    def save_snapshot(self, timestamp: datetime, state: AccountState) -> None:
        """Persist all open positions plus one equity-curve point."""


class ConfigurationRepository(ABC):
    @abstractmethod
    def upsert(self, key: str, value: str) -> None: ...


class RegimeEvaluationRepository(ABC):
    @abstractmethod
    def save(self, assessment: RegimeAssessment, timestamp: datetime) -> None: ...


class StrategyAssignmentRepository(ABC):
    @abstractmethod
    def save(self, assignment: StrategyAssignment) -> None: ...

    @abstractmethod
    def load_current(self) -> dict[str, StrategyAssignment]:
        """The newest assignment per symbol (restart recovery)."""


class RunRepository(ABC):
    @abstractmethod
    def create(
        self,
        run_type: RunType,
        config: dict[str, Any],
        symbols: Sequence[str],
        started_at: datetime,
        backtest_start: datetime | None = None,
        backtest_end: datetime | None = None,
    ) -> UUID: ...

    @abstractmethod
    def finish(self, run_id: UUID, finished_at: datetime) -> None: ...

    @abstractmethod
    def get(self, run_id: UUID) -> RunInfo | None: ...

    @abstractmethod
    def list_run_ids(self, run_type: RunType | None = None) -> list[UUID]: ...

    @abstractmethod
    def delete(self, run_id: UUID) -> None:
        """Delete the run; all of its data rows cascade."""


class BacktestMetricsRepository(ABC):
    @abstractmethod
    def save_metrics(self, run_id: UUID, metrics: Sequence[MetricValue]) -> None:
        """Replace the run's metrics (re-analysis is idempotent)."""


class RoundTripRepository(ABC):
    @abstractmethod
    def save_all(self, run_id: UUID, round_trips: Sequence[RoundTrip]) -> None:
        """Replace round trips for the run (idempotent for re-analysis)."""


class AnalysisReader(ABC):
    """Read-back of one run's persisted data for the analyzer."""

    @abstractmethod
    def list_trades(self, run_id: UUID) -> list[TradeRecord]: ...

    @abstractmethod
    def list_equity(self, run_id: UUID) -> list[EquityPoint]: ...

    @abstractmethod
    def list_decisions(self, run_id: UUID) -> list[DecisionRecord]: ...

    @abstractmethod
    def list_assignments(self, run_id: UUID) -> list[AssignmentRecord]: ...


class SqlAlchemyStrategyDecisionRepository(StrategyDecisionRepository):
    def __init__(self, session_factory: sessionmaker[Session], run_id: UUID) -> None:
        self._session_factory = session_factory
        self._run_id = run_id

    def save(self, signal: Signal, risk: RiskDecision | None, timestamp: datetime) -> None:
        with self._session_factory.begin() as session:
            session.add(
                StrategyDecisionRow(
                    run_id=self._run_id,
                    timestamp=timestamp,
                    symbol=signal.symbol,
                    strategy=signal.strategy,
                    signal=signal.action.value,
                    reason=signal.reason,
                    indicators=dict(signal.indicators),
                    risk_approved=risk.approved if risk else None,
                    risk_reason=risk.reason if risk else None,
                    risk_qty=Decimal(str(risk.qty)) if risk else None,
                )
            )


class SqlAlchemyOrderRepository(OrderRepository):
    def __init__(self, session_factory: sessionmaker[Session], run_id: UUID) -> None:
        self._session_factory = session_factory
        self._run_id = run_id

    def save_submitted(self, request: OrderRequest, result: OrderResult) -> None:
        with self._session_factory.begin() as session:
            session.add(
                OrderRow(
                    run_id=self._run_id,
                    broker_order_id=result.broker_order_id,
                    client_order_id=result.client_order_id,
                    symbol=result.symbol,
                    side=result.side.value,
                    qty=Decimal(str(result.qty)),
                    status=result.status.value,
                    created_at=result.submitted_at,
                    filled_at=result.filled_at,
                    filled_avg_price=(
                        Decimal(str(result.filled_avg_price))
                        if result.filled_avg_price is not None
                        else None
                    ),
                )
            )

    def update_from_broker(self, result: OrderResult) -> None:
        with self._session_factory.begin() as session:
            row = session.scalars(
                select(OrderRow).where(OrderRow.broker_order_id == result.broker_order_id)
            ).one()
            row.status = result.status.value
            row.filled_at = result.filled_at
            if result.filled_avg_price is not None:
                row.filled_avg_price = Decimal(str(result.filled_avg_price))

    def find_open_broker_order_ids(self) -> list[str]:
        terminal = [status.value for status in TERMINAL_ORDER_STATUSES]
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(OrderRow.broker_order_id).where(
                        OrderRow.run_id == self._run_id, OrderRow.status.not_in(terminal)
                    )
                )
            )

    def find_open_orders(self, symbol: str) -> list[OpenOrder]:
        terminal = [status.value for status in TERMINAL_ORDER_STATUSES]
        with self._session_factory() as session:
            rows = session.execute(
                select(OrderRow.broker_order_id, OrderRow.side).where(
                    OrderRow.run_id == self._run_id,
                    OrderRow.symbol == symbol,
                    OrderRow.status.not_in(terminal),
                )
            )
            return [
                OpenOrder(broker_order_id=broker_order_id, symbol=symbol, side=Side(side))
                for broker_order_id, side in rows
            ]


class SqlAlchemyTradeRepository(TradeRepository):
    def __init__(self, session_factory: sessionmaker[Session], run_id: UUID) -> None:
        self._session_factory = session_factory
        self._run_id = run_id

    def save_fill(self, result: OrderResult) -> None:
        with self._session_factory.begin() as session:
            session.add(
                TradeRow(
                    run_id=self._run_id,
                    broker_order_id=result.broker_order_id,
                    symbol=result.symbol,
                    side=result.side.value,
                    qty=Decimal(str(result.filled_qty)),
                    price=Decimal(str(result.filled_avg_price or 0)),
                    timestamp=result.filled_at or result.submitted_at,
                )
            )

    def exists_for_order(self, broker_order_id: str) -> bool:
        with self._session_factory() as session:
            return (
                session.scalars(
                    select(TradeRow.id).where(
                        TradeRow.run_id == self._run_id,
                        TradeRow.broker_order_id == broker_order_id,
                    )
                ).first()
                is not None
            )


class SqlAlchemyPositionSnapshotRepository(PositionSnapshotRepository):
    def __init__(self, session_factory: sessionmaker[Session], run_id: UUID) -> None:
        self._session_factory = session_factory
        self._run_id = run_id

    def save_snapshot(self, timestamp: datetime, state: AccountState) -> None:
        with self._session_factory.begin() as session:
            for position in state.positions.values():
                session.add(
                    PositionSnapshotRow(
                        run_id=self._run_id,
                        timestamp=timestamp,
                        symbol=position.symbol,
                        qty=Decimal(str(position.qty)),
                        avg_price=Decimal(str(position.avg_entry_price)),
                        market_value=Decimal(str(position.market_value)),
                        unrealized_pnl=Decimal(str(position.unrealized_pnl)),
                    )
                )
            session.add(
                EquitySnapshotRow(
                    run_id=self._run_id,
                    timestamp=timestamp,
                    equity=Decimal(str(state.equity)),
                    cash=Decimal(str(state.cash)),
                    buying_power=Decimal(str(state.buying_power)),
                )
            )


class SqlAlchemyRegimeEvaluationRepository(RegimeEvaluationRepository):
    def __init__(self, session_factory: sessionmaker[Session], run_id: UUID) -> None:
        self._session_factory = session_factory
        self._run_id = run_id

    def save(self, assessment: RegimeAssessment, timestamp: datetime) -> None:
        with self._session_factory.begin() as session:
            session.add(
                RegimeEvaluationRow(
                    run_id=self._run_id,
                    timestamp=timestamp,
                    symbol=assessment.symbol,
                    regime=assessment.regime.value,
                    score=assessment.score,
                    directional_score=assessment.directional_score,
                    stability=assessment.stability,
                    features=evidence_payload(assessment),
                    probabilities=probabilities_payload(assessment),
                )
            )


class SqlAlchemyStrategyAssignmentRepository(StrategyAssignmentRepository):
    def __init__(self, session_factory: sessionmaker[Session], run_id: UUID) -> None:
        self._session_factory = session_factory
        self._run_id = run_id

    def save(self, assignment: StrategyAssignment) -> None:
        with self._session_factory.begin() as session:
            session.add(
                StrategyAssignmentRow(
                    run_id=self._run_id,
                    symbol=assignment.symbol,
                    strategy=assignment.strategy,
                    regime=assignment.regime.value,
                    since=assignment.since,
                )
            )

    def load_current(self) -> dict[str, StrategyAssignment]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(StrategyAssignmentRow)
                .where(StrategyAssignmentRow.run_id == self._run_id)
                .distinct(StrategyAssignmentRow.symbol)
                .order_by(StrategyAssignmentRow.symbol, StrategyAssignmentRow.since.desc())
            )
            return {
                row.symbol: StrategyAssignment(
                    symbol=row.symbol,
                    strategy=row.strategy,
                    regime=Regime(row.regime),
                    since=row.since,
                )
                for row in rows
            }


class SqlAlchemyRunRepository(RunRepository):
    """Unscoped by design: manages the runs themselves."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        run_type: RunType,
        config: dict[str, Any],
        symbols: Sequence[str],
        started_at: datetime,
        backtest_start: datetime | None = None,
        backtest_end: datetime | None = None,
    ) -> UUID:
        run_id = uuid4()
        with self._session_factory.begin() as session:
            session.add(
                RunRow(
                    run_id=run_id,
                    run_type=run_type.value,
                    started_at=started_at,
                    finished_at=None,
                    config=config,
                    symbols=list(symbols),
                    backtest_start=backtest_start,
                    backtest_end=backtest_end,
                    note=None,
                )
            )
        return run_id

    def finish(self, run_id: UUID, finished_at: datetime) -> None:
        with self._session_factory.begin() as session:
            row = session.get(RunRow, run_id)
            if row is not None:
                row.finished_at = finished_at

    def get(self, run_id: UUID) -> RunInfo | None:
        with self._session_factory() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                return None
            return RunInfo(
                run_id=row.run_id,
                run_type=RunType(row.run_type),
                started_at=row.started_at,
                finished_at=row.finished_at,
                symbols=list(row.symbols),
                backtest_start=row.backtest_start,
                backtest_end=row.backtest_end,
                config=dict(row.config),
            )

    def list_run_ids(self, run_type: RunType | None = None) -> list[UUID]:
        stmt = select(RunRow.run_id).order_by(RunRow.started_at)
        if run_type is not None:
            stmt = stmt.where(RunRow.run_type == run_type.value)
        with self._session_factory() as session:
            return list(session.scalars(stmt))

    def delete(self, run_id: UUID) -> None:
        with self._session_factory.begin() as session:
            session.execute(delete(RunRow).where(RunRow.run_id == run_id))


class SqlAlchemyBacktestMetricsRepository(BacktestMetricsRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_metrics(self, run_id: UUID, metrics: Sequence[MetricValue]) -> None:
        with self._session_factory.begin() as session:
            session.execute(delete(BacktestMetricRow).where(BacktestMetricRow.run_id == run_id))
            for metric in metrics:
                session.add(
                    BacktestMetricRow(
                        run_id=run_id,
                        name=metric.name,
                        symbol=metric.symbol,
                        value=Decimal(str(metric.value)),
                    )
                )


class SqlAlchemyRoundTripRepository(RoundTripRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_all(self, run_id: UUID, round_trips: Sequence[RoundTrip]) -> None:
        with self._session_factory.begin() as session:
            session.execute(delete(RoundTripRow).where(RoundTripRow.run_id == run_id))
            for trip in round_trips:
                holding = int((trip.closed_at - trip.opened_at).total_seconds())
                session.add(
                    RoundTripRow(
                        run_id=run_id,
                        symbol=trip.symbol,
                        qty=Decimal(str(trip.qty)),
                        entry_time=trip.opened_at,
                        exit_time=trip.closed_at,
                        entry_price=Decimal(str(trip.entry_price)),
                        exit_price=Decimal(str(trip.exit_price)),
                        pnl=Decimal(str(trip.pnl)),
                        holding_seconds=holding,
                        strategy=trip.strategy,
                        exit_reason=trip.exit_reason,
                        regime=trip.regime,
                        entry_indicators=dict(trip.entry_indicators),
                        exit_indicators=dict(trip.exit_indicators),
                    )
                )


class SqlAlchemyAnalysisReader(AnalysisReader):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_trades(self, run_id: UUID) -> list[TradeRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(TradeRow).where(TradeRow.run_id == run_id).order_by(TradeRow.timestamp)
            )
            return [
                TradeRecord(
                    symbol=row.symbol,
                    side=Side(row.side),
                    qty=float(row.qty),
                    price=float(row.price),
                    timestamp=row.timestamp,
                )
                for row in rows
            ]

    def list_equity(self, run_id: UUID) -> list[EquityPoint]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(EquitySnapshotRow)
                .where(EquitySnapshotRow.run_id == run_id)
                .order_by(EquitySnapshotRow.timestamp)
            )
            return [EquityPoint(timestamp=row.timestamp, equity=float(row.equity)) for row in rows]

    def list_decisions(self, run_id: UUID) -> list[DecisionRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(StrategyDecisionRow)
                .where(
                    StrategyDecisionRow.run_id == run_id,
                    StrategyDecisionRow.signal.in_(["buy", "sell"]),
                )
                .order_by(StrategyDecisionRow.timestamp)
            )
            return [
                DecisionRecord(
                    symbol=row.symbol,
                    side=Side(row.signal),
                    timestamp=row.timestamp,
                    reason=row.reason,
                    indicators={k: float(v) for k, v in row.indicators.items()},
                    strategy=row.strategy,
                )
                for row in rows
            ]

    def list_assignments(self, run_id: UUID) -> list[AssignmentRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(StrategyAssignmentRow)
                .where(StrategyAssignmentRow.run_id == run_id)
                .order_by(StrategyAssignmentRow.since)
            )
            return [
                AssignmentRecord(
                    symbol=row.symbol,
                    since=row.since,
                    regime=row.regime,
                    strategy=row.strategy,
                )
                for row in rows
            ]


class SqlAlchemyConfigurationRepository(ConfigurationRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert(self, key: str, value: str) -> None:
        with self._session_factory.begin() as session:
            row = session.scalars(
                select(ConfigurationRow).where(ConfigurationRow.key == key)
            ).one_or_none()
            if row is None:
                session.add(ConfigurationRow(key=key, value=value))
            else:
                row.value = value
