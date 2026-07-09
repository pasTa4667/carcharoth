"""Repository interfaces and their SQLAlchemy implementations.

Each repository opens a short transaction per call; nothing holds a session
across engine steps. Tests use in-memory fakes of the ABCs instead.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from carcharoth.domain.models import (
    TERMINAL_ORDER_STATUSES,
    OpenOrder,
    OrderRequest,
    OrderResult,
    Position,
    RiskDecision,
    Side,
    Signal,
)
from carcharoth.persistence.orm import (
    ConfigurationRow,
    OrderRow,
    PositionSnapshotRow,
    RegimeEvaluationRow,
    StrategyAssignmentRow,
    StrategyDecisionRow,
    TradeRow,
)
from carcharoth.regime.models import Regime, RegimeAssessment, StrategyAssignment


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
    def save_snapshot(self, timestamp: datetime, positions: Iterable[Position]) -> None: ...


class ConfigurationRepository(ABC):
    @abstractmethod
    def upsert(self, key: str, value: str) -> None: ...


class RegimeEvaluationRepository(ABC):
    @abstractmethod
    def save(
        self, assessment: RegimeAssessment, weights: Mapping[str, float], timestamp: datetime
    ) -> None: ...


class StrategyAssignmentRepository(ABC):
    @abstractmethod
    def save(self, assignment: StrategyAssignment) -> None: ...

    @abstractmethod
    def load_current(self) -> dict[str, StrategyAssignment]:
        """The newest assignment per symbol (restart recovery)."""


class SqlAlchemyStrategyDecisionRepository(StrategyDecisionRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, signal: Signal, risk: RiskDecision | None, timestamp: datetime) -> None:
        with self._session_factory.begin() as session:
            session.add(
                StrategyDecisionRow(
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
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_submitted(self, request: OrderRequest, result: OrderResult) -> None:
        with self._session_factory.begin() as session:
            session.add(
                OrderRow(
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
                    select(OrderRow.broker_order_id).where(OrderRow.status.not_in(terminal))
                )
            )

    def find_open_orders(self, symbol: str) -> list[OpenOrder]:
        terminal = [status.value for status in TERMINAL_ORDER_STATUSES]
        with self._session_factory() as session:
            rows = session.execute(
                select(OrderRow.broker_order_id, OrderRow.side).where(
                    OrderRow.symbol == symbol, OrderRow.status.not_in(terminal)
                )
            )
            return [
                OpenOrder(broker_order_id=broker_order_id, symbol=symbol, side=Side(side))
                for broker_order_id, side in rows
            ]


class SqlAlchemyTradeRepository(TradeRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_fill(self, result: OrderResult) -> None:
        with self._session_factory.begin() as session:
            session.add(
                TradeRow(
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
                    select(TradeRow.id).where(TradeRow.broker_order_id == broker_order_id)
                ).first()
                is not None
            )


class SqlAlchemyPositionSnapshotRepository(PositionSnapshotRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_snapshot(self, timestamp: datetime, positions: Iterable[Position]) -> None:
        with self._session_factory.begin() as session:
            for position in positions:
                session.add(
                    PositionSnapshotRow(
                        timestamp=timestamp,
                        symbol=position.symbol,
                        qty=Decimal(str(position.qty)),
                        avg_price=Decimal(str(position.avg_entry_price)),
                        market_value=Decimal(str(position.market_value)),
                        unrealized_pnl=Decimal(str(position.unrealized_pnl)),
                    )
                )


class SqlAlchemyRegimeEvaluationRepository(RegimeEvaluationRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(
        self, assessment: RegimeAssessment, weights: Mapping[str, float], timestamp: datetime
    ) -> None:
        features = {
            e.feature: {
                "value": e.value,
                "direction": e.direction,
                "stability": e.stability,
                "weight": weights.get(e.feature),
            }
            for e in assessment.evidence
        }
        with self._session_factory.begin() as session:
            session.add(
                RegimeEvaluationRow(
                    timestamp=timestamp,
                    symbol=assessment.symbol,
                    regime=assessment.regime.value,
                    score=assessment.score,
                    directional_score=assessment.directional_score,
                    stability=assessment.stability,
                    features=features,
                )
            )


class SqlAlchemyStrategyAssignmentRepository(StrategyAssignmentRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, assignment: StrategyAssignment) -> None:
        with self._session_factory.begin() as session:
            session.add(
                StrategyAssignmentRow(
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
