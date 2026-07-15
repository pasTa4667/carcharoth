"""Replays historical data through the unchanged TradingEngine.

No scheduler: the runner iterates the historical bar grid as fast as the
app can process it. One tick == one bar; time-based settings such as the
regime detectors' `evaluate_interval_minutes` follow the bar timestamps,
so they mean the same market time here as in live trading.
"""

import logging
from datetime import datetime

from tqdm import tqdm

from carcharoth.domain.models import OrderStatus
from carcharoth.engine.engine import TradingEngine
from carcharoth.interfaces.execution import OrderExecutor
from carcharoth.persistence.repositories import OrderRepository, TradeRepository
from carcharoth.services.backtest.broker import SimulatedBroker
from carcharoth.services.backtest.market_data import HistoricalMarketDataService
from carcharoth.strategies.session import minutes_since_open, minutes_until_close

logger = logging.getLogger(__name__)

_PROGRESS_EVERY_BARS = 100


class BacktestRunner:
    def __init__(
        self,
        engine: TradingEngine,
        market_data: HistoricalMarketDataService,
        broker: SimulatedBroker,
        orders_repo: OrderRepository,
        trades_repo: TradeRepository,
        executor: OrderExecutor,
        start: datetime,
        end: datetime,
        show_progress: bool = False,
    ) -> None:
        self._engine = engine
        self._market_data = market_data
        self._broker = broker
        self._orders_repo = orders_repo
        self._trades_repo = trades_repo
        self._executor = executor
        self._start = start
        self._end = end
        self._show_progress = show_progress

    def run(self) -> None:
        # Alpaca minute bars include pre-/after-hours; the live scheduler only
        # ticks while the market is open, so replay regular-session bars only.
        grid = [
            as_of
            for as_of in self._market_data.timestamp_grid(self._start, self._end)
            if minutes_since_open(as_of) >= 0 and minutes_until_close(as_of) > 0
        ]
        if not grid:
            logger.warning("no historical bars between %s and %s", self._start, self._end)
            return
        logger.info("backtest: replaying %d bars from %s to %s", len(grid), grid[0], grid[-1])
        # A live tqdm bar replaces the periodic log lines; when off (optimizer
        # trials, --verbose) we keep the every-N-bars INFO progress instead.
        iterable = (
            tqdm(grid, total=len(grid), unit="bar", desc="backtest")
            if self._show_progress
            else grid
        )
        for index, as_of in enumerate(iterable, start=1):
            self._market_data.advance_to(as_of)
            self._broker.mark_to_market(as_of, self._market_data.latest_closes())
            self._engine.tick()
            if not self._show_progress and (
                index % _PROGRESS_EVERY_BARS == 0 or index == len(grid)
            ):
                logger.info("backtest: bar %d/%d (%s)", index, len(grid), as_of)
        self._reconcile_remaining()

    def _reconcile_remaining(self) -> None:
        """Record fills of orders submitted on the last bar.

        The engine reconciles at the START of each tick (see
        TradingEngine._reconcile_fills), so without this final pass a
        last-bar order would stay ACCEPTED and its trade never recorded.
        """
        for broker_order_id in self._orders_repo.find_open_broker_order_ids():
            result = self._executor.get_order(broker_order_id)
            self._orders_repo.update_from_broker(result)
            if result.status is OrderStatus.FILLED and not self._trades_repo.exists_for_order(
                broker_order_id
            ):
                self._trades_repo.save_fill(result)
