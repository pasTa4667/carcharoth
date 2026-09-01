"""The trading engine: a deliberately "stupid" orchestrator.

It only sequences the services and passes data between them. All trading
logic lives in the model generator, risk manager and adapter implementations —
never here. Swapping any component requires no change to this file.
"""

import logging
from uuid import uuid4

from carcharoth.domain.errors import BrokerError, MarketDataError, OrderConflictError
from carcharoth.domain.models import (
    AccountState,
    MarketSnapshot,
    OrderRequest,
    OrderStatus,
    Side,
    TargetExposure,
)
from carcharoth.interfaces import (
    AccountService,
    MarketDataService,
    OrderExecutor,
    RiskManager,
)
from carcharoth.interfaces.model_signal import ModelSignalGenerator
from carcharoth.logging_setup import DECISIONS_LOGGER, TRADES_LOGGER
from carcharoth.persistence.repositories import (
    ModelDecisionRepository,
    OrderRepository,
    PositionSnapshotRepository,
    TradeRepository,
)

logger = logging.getLogger(__name__)
trades_log = logging.getLogger(TRADES_LOGGER)
decisions_log = logging.getLogger(DECISIONS_LOGGER)


class TradingEngine:
    def __init__(
        self,
        market_data: MarketDataService,
        account: AccountService,
        model: ModelSignalGenerator,
        risk: RiskManager,
        executor: OrderExecutor,
        decisions_repo: ModelDecisionRepository,
        orders_repo: OrderRepository,
        trades_repo: TradeRepository,
        snapshots_repo: PositionSnapshotRepository,
        symbols: list[str],
    ) -> None:
        self._market_data = market_data
        self._account = account
        self._model = model
        self._risk = risk
        self._executor = executor
        self._decisions_repo = decisions_repo
        self._orders_repo = orders_repo
        self._trades_repo = trades_repo
        self._snapshots_repo = snapshots_repo
        self._symbols = symbols

    def tick(self) -> None:
        self._reconcile_fills()

        requirements = self._model.market_requirements()
        requested_symbols = list(
            dict.fromkeys([*self._symbols, *sorted(requirements.context_symbols)])
        )
        try:
            snapshot = self._market_data.get_snapshot(requested_symbols, requirements.bars)
        except MarketDataError:
            logger.exception("market data unavailable, aborting tick")
            return

        state = self._account.get_account_state()
        self._snapshots_repo.save_snapshot(snapshot.as_of, state)

        for symbol in self._symbols:
            try:
                self._process_symbol(symbol, snapshot, state)
            except Exception:
                logger.exception("error processing %s, continuing with next symbol", symbol)

    def _process_symbol(self, symbol: str, snapshot: MarketSnapshot, state: AccountState) -> None:
        quote = snapshot.quotes.get(symbol)
        position = state.positions.get(symbol)
        intent = self._model.generate(symbol, snapshot, position)
        decisions_log.info(
            "%s target=%s model=%s reason=%r prediction=%s features=%s",
            symbol,
            intent.target.value,
            intent.model_id,
            intent.reason,
            intent.prediction.as_dict() if intent.prediction else {},
            intent.features.as_dict() if intent.features else {},
        )

        if intent.target is TargetExposure.UNCHANGED or quote is None:
            self._decisions_repo.save(intent, None, snapshot.as_of)
            return

        decision = self._risk.assess(intent, state, quote)
        decisions_log.info(
            "%s risk approved=%s reason=%r", symbol, decision.approved, decision.reason
        )
        self._decisions_repo.save(intent, decision, snapshot.as_of)
        if not decision.approved or decision.side is None:
            return

        side = decision.side
        if not self._clear_conflicting_orders(symbol, side):
            return

        request = OrderRequest(
            symbol=symbol,
            side=side,
            qty=decision.qty,
            client_order_id=uuid4().hex,
        )
        try:
            result = self._executor.submit(request)
        except OrderConflictError as exc:
            trades_log.warning("%s: broker wash-trade rejection, skipping tick: %s", symbol, exc)
            return
        self._orders_repo.save_submitted(request, result)
        trades_log.info(
            "submitted %s %s x%s @ bid=%.2f ask=%.2f -> broker_order_id=%s status=%s",
            request.side.value,
            request.symbol,
            request.qty,
            quote.bid_price,
            quote.ask_price,
            result.broker_order_id,
            result.status.value,
        )

    def _clear_conflicting_orders(self, symbol: str, side: Side) -> bool:
        """Return True when it is safe to submit a new `side` order for `symbol`.

        Same-side open order: a duplicate is already in flight -> skip.
        Opposite-side open order: request cancellation and skip this tick;
        Alpaca cancels asynchronously, so submitting now would trip its
        wash-trade protection (or double-execute if the old order fills
        during the cancel). The signal re-fires next tick.
        """
        open_orders = self._orders_repo.find_open_orders(symbol)
        if not open_orders:
            return True
        for order in open_orders:
            if order.side is side:
                trades_log.info(
                    "%s: skipping %s, same-side order %s still open",
                    symbol,
                    side.value,
                    order.broker_order_id,
                )
                continue
            trades_log.info(
                "%s: canceling opposite-side order %s before %s (retry next tick)",
                symbol,
                order.broker_order_id,
                side.value,
            )
            try:
                self._executor.cancel_order(order.broker_order_id)
            except BrokerError:
                logger.exception("could not cancel order %s", order.broker_order_id)
        return False

    def _reconcile_fills(self) -> None:
        for broker_order_id in self._orders_repo.find_open_broker_order_ids():
            try:
                result = self._executor.get_order(broker_order_id)
            except Exception:
                logger.exception("could not reconcile order %s", broker_order_id)
                continue
            self._orders_repo.update_from_broker(result)
            if result.status is OrderStatus.FILLED and not self._trades_repo.exists_for_order(
                broker_order_id
            ):
                self._trades_repo.save_fill(result)
                trades_log.info(
                    "filled %s %s x%s @ %s",
                    result.side.value,
                    result.symbol,
                    result.filled_qty,
                    result.filled_avg_price,
                )
