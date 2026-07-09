"""Composition root: builds and wires all components.

This is the ONLY place that decides which concrete implementations run.
To swap a provider (e.g. another broker) or strategy, change the wiring
here — no other module needs to be touched.
"""

import logging
import signal
import types
from pathlib import Path

from carcharoth.config.app_config import load_config
from carcharoth.config.settings import Settings
from carcharoth.engine.engine import TradingEngine
from carcharoth.engine.scheduler import Scheduler
from carcharoth.logging_setup import setup_logging
from carcharoth.persistence.db import build_engine, build_session_factory
from carcharoth.persistence.repositories import (
    SqlAlchemyConfigurationRepository,
    SqlAlchemyOrderRepository,
    SqlAlchemyPositionSnapshotRepository,
    SqlAlchemyStrategyDecisionRepository,
    SqlAlchemyTradeRepository,
)
from carcharoth.risk.basic import BasicRiskManager
from carcharoth.services.alpaca import (
    AlpacaAccountService,
    AlpacaMarketClock,
    AlpacaMarketDataService,
    AlpacaOrderExecutor,
    build_data_client,
    build_trading_client,
)
from carcharoth.services.cache.noop import NoOpCache
from carcharoth.strategies.registry import build_strategy

logger = logging.getLogger(__name__)

# All paths (including the .env read by Settings) are resolved relative to
# the working directory; run the bot from the project root.
CONFIG_PATH = Path("config/config.yaml")
LOG_DIR = Path("logs")


def main() -> None:
    setup_logging(LOG_DIR)

    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    config = load_config(CONFIG_PATH)

    db_engine = build_engine(settings.database_url)
    session_factory = build_session_factory(db_engine)

    # Audit trail: persist the effective configuration this run started with.
    SqlAlchemyConfigurationRepository(session_factory).upsert(
        "effective_config", config.model_dump_json()
    )

    trading_client = build_trading_client(settings)
    data_client = build_data_client(settings)

    engine = TradingEngine(
        market_data=AlpacaMarketDataService(data_client, cache=NoOpCache()),
        account=AlpacaAccountService(trading_client),
        strategy=build_strategy(config.strategy.name, config.strategy.params),
        risk=BasicRiskManager(config.risk),
        executor=AlpacaOrderExecutor(trading_client),
        decisions_repo=SqlAlchemyStrategyDecisionRepository(session_factory),
        orders_repo=SqlAlchemyOrderRepository(session_factory),
        trades_repo=SqlAlchemyTradeRepository(session_factory),
        snapshots_repo=SqlAlchemyPositionSnapshotRepository(session_factory),
        symbols=config.watchlist.symbols,
    )
    scheduler = Scheduler(
        engine,
        clock=AlpacaMarketClock(trading_client),
        interval_seconds=config.engine.tick_interval_seconds,
    )

    def handle_signal(signum: int, frame: types.FrameType | None) -> None:
        scheduler.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info(
        "starting carcharoth: strategy=%s watchlist=%s",
        config.strategy.name,
        config.watchlist.symbols,
    )
    try:
        scheduler.run_forever()
    finally:
        db_engine.dispose()
        logger.info("shutdown complete")


if __name__ == "__main__":
    main()
