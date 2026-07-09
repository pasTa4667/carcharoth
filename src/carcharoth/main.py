"""Composition root: builds and wires all components.

This is the ONLY place that decides which concrete implementations run.
To swap a provider (e.g. another broker) or strategy, change the wiring
here — no other module needs to be touched.
"""

import logging
import signal
import types
from pathlib import Path
from typing import TYPE_CHECKING

from carcharoth.config.app_config import AppConfig, load_config
from carcharoth.config.settings import Settings
from carcharoth.engine.engine import TradingEngine
from carcharoth.engine.scheduler import Scheduler
from carcharoth.engine.strategy_provider import RegimeStrategyProvider, SingleStrategyProvider
from carcharoth.interfaces import StrategyProvider
from carcharoth.logging_setup import setup_logging
from carcharoth.persistence.db import build_engine, build_session_factory
from carcharoth.persistence.repositories import (
    SqlAlchemyConfigurationRepository,
    SqlAlchemyOrderRepository,
    SqlAlchemyPositionSnapshotRepository,
    SqlAlchemyRegimeEvaluationRepository,
    SqlAlchemyStrategyAssignmentRepository,
    SqlAlchemyStrategyDecisionRepository,
    SqlAlchemyTradeRepository,
)
from carcharoth.regime.detector import RegimeDetector
from carcharoth.regime.models import Regime
from carcharoth.regime.registry import build_feature
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

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

# All paths (including the .env read by Settings) are resolved relative to
# the working directory; run the bot from the project root.
CONFIG_PATH = Path("config/config.yaml")
LOG_DIR = Path("logs")


def build_strategy_provider(
    config: AppConfig, session_factory: "sessionmaker[Session]"
) -> StrategyProvider:
    if config.regime is None:
        assert config.strategy is not None  # guaranteed by AppConfig validation
        return SingleStrategyProvider(build_strategy(config.strategy.name, config.strategy.params))
    detector = RegimeDetector(
        features=[
            (build_feature(name, fc.params), fc.weight)
            for name, fc in config.regime.features.items()
        ],
        lookback=config.regime.lookback,
        winsorize_sigma=config.regime.winsorize_sigma,
    )
    return RegimeStrategyProvider(
        detector=detector,
        strategies={
            Regime(name): build_strategy(rc.strategy, rc.params)
            for name, rc in config.regime.regimes.items()
        },
        evaluations_repo=SqlAlchemyRegimeEvaluationRepository(session_factory),
        assignments_repo=SqlAlchemyStrategyAssignmentRepository(session_factory),
        evaluate_every_ticks=config.regime.evaluate_every_ticks,
        default_regime=Regime(config.regime.default_regime),
    )


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
        strategies=build_strategy_provider(config, session_factory),
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

    if config.regime is not None:
        strategy_desc = "regime-driven " + str(
            {name: rc.strategy for name, rc in config.regime.regimes.items()}
        )
    else:
        assert config.strategy is not None  # guaranteed by AppConfig validation
        strategy_desc = config.strategy.name
    logger.info(
        "starting carcharoth: strategy=%s watchlist=%s",
        strategy_desc,
        config.watchlist.symbols,
    )
    try:
        scheduler.run_forever()
    finally:
        db_engine.dispose()
        logger.info("shutdown complete")


if __name__ == "__main__":
    main()
