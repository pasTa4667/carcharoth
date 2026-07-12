"""Composition root: builds and wires all components.

This is the ONLY place that decides which concrete implementations run.
To swap a provider (e.g. another broker) or strategy, change the wiring
here — no other module needs to be touched.

CLI: `carcharoth` (or `carcharoth run`) starts live paper trading;
`carcharoth backtest` replays historical data through the same engine;
`carcharoth analyze` recomputes a backtest's metrics;
`carcharoth delete-run` removes a run and all of its data.
"""

import argparse
import logging
import multiprocessing
import signal
import time
import types
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import yaml

from carcharoth.analysis.analyzer import BacktestAnalyzer
from carcharoth.backtest.runner import BacktestRunner
from carcharoth.config.app_config import AppConfig, RegimeConfig, StrategyConfig, load_config
from carcharoth.config.optimize_config import OptimizeConfig, load_optimize_config
from carcharoth.config.settings import Settings
from carcharoth.domain.models import BacktestResult, OptimizationResult, RunType
from carcharoth.engine.engine import TradingEngine
from carcharoth.engine.scheduler import Scheduler
from carcharoth.engine.strategy_provider import RegimeStrategyProvider, SingleStrategyProvider
from carcharoth.interfaces import BarsFetcher, StrategyProvider
from carcharoth.logging_setup import (
    setup_logging,
    write_backtest_summary,
    write_optimize_summary,
)
from carcharoth.optimize.bars_cache import BarsCache
from carcharoth.optimize.overrides import validate_override_paths
from carcharoth.persistence.buffered import (
    BufferedPositionSnapshotRepository,
    BufferedRegimeEvaluationRepository,
    BufferedStrategyDecisionRepository,
    WriteBuffer,
    sqlalchemy_flush,
)
from carcharoth.persistence.db import build_engine, build_session_factory
from carcharoth.persistence.repositories import (
    RegimeEvaluationRepository,
    RunRepository,
    SqlAlchemyAnalysisReader,
    SqlAlchemyBacktestMetricsRepository,
    SqlAlchemyConfigurationRepository,
    SqlAlchemyOrderRepository,
    SqlAlchemyPositionSnapshotRepository,
    SqlAlchemyRegimeEvaluationRepository,
    SqlAlchemyRoundTripRepository,
    SqlAlchemyRunRepository,
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
from carcharoth.services.alpaca.historical import fetch_historical_bars, warmup_window
from carcharoth.services.backtest import HistoricalMarketDataService, SimulatedBroker
from carcharoth.services.cache.noop import NoOpCache
from carcharoth.services.optuna import (
    OptunaOptimizer,
    build_worker_storage,
    create_or_load_study,
    prepare_storage_url,
    summarize_study,
)
from carcharoth.strategies.registry import build_strategy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

# All paths (including the .env read by Settings) are resolved relative to
# the working directory; run the bot from the project root.
CONFIG_PATH = Path("config/config.yaml")
LOG_DIR = Path("logs")


def build_strategy_provider(
    config: AppConfig,
    session_factory: "sessionmaker[Session]",
    run_id: UUID,
    evaluations_repo: RegimeEvaluationRepository | None = None,
) -> StrategyProvider:
    if config.regime is None or not config.regime.active:
        name, sc = _active_strategy(config)  # exactly one, guaranteed by validation
        return SingleStrategyProvider(build_strategy(name, sc.params))
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
            Regime(name): build_strategy(rc.strategy, config.strategies[rc.strategy].params)
            for name, rc in config.regime.regimes.items()
        },
        evaluations_repo=evaluations_repo
        or SqlAlchemyRegimeEvaluationRepository(session_factory, run_id),
        # Assignments are read back (load_current) and low-volume: never buffered.
        assignments_repo=SqlAlchemyStrategyAssignmentRepository(session_factory, run_id),
        evaluate_every_ticks=config.regime.evaluate_every_ticks,
        default_regime=Regime(config.regime.default_regime),
    )


def _active_strategy(config: AppConfig) -> tuple[str, StrategyConfig]:
    """The single active strategy for single-strategy mode; validation
    guarantees exactly one strategy has ``active: true``."""
    return next((name, sc) for name, sc in config.strategies.items() if sc.active)


def _summary_regime(config: AppConfig) -> RegimeConfig | None:
    """The regime block to record in a backtest summary — only when it drove
    the run, so the summary reflects the mode that actually executed."""
    return config.regime if config.regime is not None and config.regime.active else None


def _strategy_description(config: AppConfig) -> str:
    if config.regime is not None and config.regime.active:
        return "regime-driven " + str(
            {name: rc.strategy for name, rc in config.regime.regimes.items()}
        )
    return _active_strategy(config)[0]


def _run_live(config_path: Path) -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    config = load_config(config_path)

    db_engine = build_engine(settings.database_url)
    session_factory = build_session_factory(db_engine)

    # Audit trail: persist the effective configuration this run started with.
    SqlAlchemyConfigurationRepository(session_factory).upsert(
        "effective_config", config.model_dump_json()
    )
    runs_repo = SqlAlchemyRunRepository(session_factory)
    run_id = runs_repo.create(
        run_type=RunType.PAPER,
        config=config.model_dump(mode="json"),
        symbols=config.watchlist.symbols,
        started_at=datetime.now(UTC),
    )

    trading_client = build_trading_client(settings)
    data_client = build_data_client(settings)

    engine = TradingEngine(
        market_data=AlpacaMarketDataService(data_client, cache=NoOpCache()),
        account=AlpacaAccountService(trading_client),
        strategies=build_strategy_provider(config, session_factory, run_id),
        risk=BasicRiskManager(config.risk),
        executor=AlpacaOrderExecutor(trading_client),
        decisions_repo=SqlAlchemyStrategyDecisionRepository(session_factory, run_id),
        orders_repo=SqlAlchemyOrderRepository(session_factory, run_id),
        trades_repo=SqlAlchemyTradeRepository(session_factory, run_id),
        snapshots_repo=SqlAlchemyPositionSnapshotRepository(session_factory, run_id),
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
        "starting carcharoth: run_id=%s strategy=%s watchlist=%s",
        run_id,
        _strategy_description(config),
        config.watchlist.symbols,
    )
    try:
        scheduler.run_forever()
    finally:
        runs_repo.finish(run_id, datetime.now(UTC))
        db_engine.dispose()
        logger.info("shutdown complete")


def run_backtest_once(
    config: AppConfig,
    start: datetime,
    end_exclusive: datetime,
    symbols: Sequence[str],
    session_factory: "sessionmaker[Session]",
    fetch_bars: BarsFetcher,
) -> BacktestResult:
    """One fully-persisted backtest run: creates the run row, replays the
    window, analyzes. Knows nothing about its caller — manual CLI runs and
    optimizer trials are indistinguishable."""
    runs_repo = SqlAlchemyRunRepository(session_factory)
    started_at = datetime.now(UTC)
    run_id = runs_repo.create(
        run_type=RunType.BACKTEST,
        config=config.model_dump(mode="json"),
        symbols=symbols,
        started_at=started_at,
        backtest_start=start,
        backtest_end=end_exclusive,
    )
    logger.info(
        "backtest run %s: %s to %s, strategy=%s symbols=%s",
        run_id,
        start.date(),
        (end_exclusive - timedelta(days=1)).date(),
        _strategy_description(config),
        list(symbols),
    )

    # High-volume append-only rows are buffered and bulk-inserted; orders
    # and trades are read back every tick and stay on per-call repositories.
    buffer = WriteBuffer(sqlalchemy_flush(session_factory))

    # The provider dictates the bar timeframe and how much warm-up history
    # the first tick needs; the backtest prefetches accordingly.
    provider = build_strategy_provider(
        config,
        session_factory,
        run_id,
        evaluations_repo=BufferedRegimeEvaluationRepository(buffer, run_id),
    )
    spec = provider.required_bars()
    bars = fetch_bars(symbols, spec.timeframe, start - warmup_window(spec), end_exclusive)
    total_bars = sum(len(symbol_bars) for symbol_bars in bars.values())
    logger.info("fetched %d bars for %d symbols", total_bars, len(symbols))

    market_data = HistoricalMarketDataService(bars, spread_pct=config.backtest.spread_pct)
    broker = SimulatedBroker(
        initial_capital=config.backtest.initial_capital,
        spread_pct=config.backtest.spread_pct,
        slippage_pct=config.backtest.slippage_pct,
    )
    orders_repo = SqlAlchemyOrderRepository(session_factory, run_id)
    trades_repo = SqlAlchemyTradeRepository(session_factory, run_id)
    engine = TradingEngine(
        market_data=market_data,
        account=broker,
        strategies=provider,
        risk=BasicRiskManager(config.risk),
        executor=broker,
        decisions_repo=BufferedStrategyDecisionRepository(buffer, run_id),
        orders_repo=orders_repo,
        trades_repo=trades_repo,
        snapshots_repo=BufferedPositionSnapshotRepository(buffer, run_id),
        symbols=list(symbols),
    )
    runner = BacktestRunner(
        engine=engine,
        market_data=market_data,
        broker=broker,
        orders_repo=orders_repo,
        trades_repo=trades_repo,
        executor=broker,
        start=start,
        end=end_exclusive,
    )
    try:
        runner.run()
    finally:
        # The analyzer below reads this run's rows: everything buffered must
        # be on disk first.
        buffer.flush()
    runs_repo.finish(run_id, datetime.now(UTC))

    metrics = BacktestAnalyzer(
        reader=SqlAlchemyAnalysisReader(session_factory),
        metrics_repo=SqlAlchemyBacktestMetricsRepository(session_factory),
        objectives=config.objectives,
        round_trips_repo=SqlAlchemyRoundTripRepository(session_factory),
    ).analyze(run_id)
    write_backtest_summary(
        LOG_DIR, run_id, started_at, _summary_regime(config), config.risk, metrics
    )
    logger.info("backtest run %s complete", run_id)
    return BacktestResult(run_id=run_id, metrics=metrics)


def _run_backtest(
    config_path: Path, start: datetime, end: datetime, symbols: list[str] | None
) -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    config = load_config(config_path)
    watchlist = symbols if symbols else config.watchlist.symbols
    end_exclusive = end + timedelta(days=1)  # --end is inclusive

    db_engine = build_engine(settings.database_url)
    session_factory = build_session_factory(db_engine)
    fetch_bars = partial(fetch_historical_bars, build_data_client(settings))
    try:
        run_backtest_once(config, start, end_exclusive, watchlist, session_factory, fetch_bars)
    finally:
        db_engine.dispose()


def _run_optimize(
    config_path: Path,
    optimize_config_path: Path,
    n_trials: int | None,
    study_name: str | None,
    workers: int | None,
) -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    # The optimizer applies dot-path overrides to the raw dict, so the raw
    # YAML is kept alongside the validated config.
    with config_path.open() as f:
        raw_config = yaml.safe_load(f)
    config = AppConfig.model_validate(raw_config)
    optimize_config = load_optimize_config(optimize_config_path)

    objective = config.objectives.get(optimize_config.objective)
    if objective is None:
        raise SystemExit(
            f"objective {optimize_config.objective!r} is not defined in {config_path} "
            f"(available: {sorted(config.objectives)})"
        )
    symbols = optimize_config.backtest.symbols or config.watchlist.symbols
    # Optuna gets its own schema in the shared Postgres (its internal
    # alembic_version table would otherwise collide with carcharoth's).
    storage_url = prepare_storage_url(settings.optuna_database_url or settings.database_url)

    effective_workers = workers or optimize_config.study.workers
    if effective_workers < 1:
        raise SystemExit("--workers must be >= 1")
    if effective_workers > 1:
        _run_optimize_parallel(
            config_path=config_path,
            optimize_config_path=optimize_config_path,
            raw_config=raw_config,
            optimize_config=optimize_config,
            storage_url=storage_url,
            n_trials=n_trials or optimize_config.study.n_trials,
            study_name=study_name or optimize_config.study.name,
            workers=effective_workers,
        )
        return

    db_engine = build_engine(settings.database_url)
    session_factory = build_session_factory(db_engine)
    # One data client + in-process bars cache for the whole study: trials
    # re-fetch nothing unless their warm-up window grows.
    fetch_bars: BarsFetcher = BarsCache(partial(fetch_historical_bars, build_data_client(settings)))

    def run_trial_backtest(
        config: AppConfig, start: datetime, end_exclusive: datetime, symbols: Sequence[str]
    ) -> BacktestResult:
        return run_backtest_once(config, start, end_exclusive, symbols, session_factory, fetch_bars)

    optimizer = OptunaOptimizer(
        run_backtest=run_trial_backtest,
        raw_config=raw_config,
        optimize_config=optimize_config,
        objective=objective,
        symbols=symbols,
        storage=storage_url,
        n_trials=n_trials,
        study_name=study_name,
    )
    try:
        result = optimizer.optimize()
    finally:
        db_engine.dispose()

    write_optimize_summary(LOG_DIR, datetime.now(UTC), optimize_config.objective, result)
    _log_optimize_result(result, storage_url)


def _split_trials(total: int, workers: int) -> list[int]:
    """Split a trial budget across workers; earlier workers take the remainder."""
    base, extra = divmod(total, workers)
    return [base + (1 if index < extra else 0) for index in range(workers)]


def _optimize_worker(
    config_path: Path,
    optimize_config_path: Path,
    study_name: str,
    n_trials: int,
    worker_index: int,
    sampler_seed: int | None,
) -> None:
    """One parallel optimize worker (multiprocessing spawn target).

    Workers coordinate purely through Optuna's shared storage: each runs its
    share of trials with its own DB engines, Alpaca client, bars cache and
    log files. The parent pre-created the study and writes the summary.
    """
    setup_logging(LOG_DIR, console_level="WARNING", filename_suffix=f".w{worker_index}")
    # Stagger startup so the workers' initial full-window bar fetches don't
    # hit Alpaca in the same instant (the bars cache is per-process).
    time.sleep(worker_index * 2.0)

    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    with config_path.open() as f:
        raw_config = yaml.safe_load(f)
    config = AppConfig.model_validate(raw_config)
    optimize_config = load_optimize_config(optimize_config_path)
    objective = config.objectives[optimize_config.objective]  # parent validated
    symbols = optimize_config.backtest.symbols or config.watchlist.symbols
    storage_url = prepare_storage_url(settings.optuna_database_url or settings.database_url)

    db_engine = build_engine(settings.database_url, pool_size=2, max_overflow=2)
    session_factory = build_session_factory(db_engine)
    fetch_bars: BarsFetcher = BarsCache(partial(fetch_historical_bars, build_data_client(settings)))

    def run_trial_backtest(
        config: AppConfig, start: datetime, end_exclusive: datetime, symbols: Sequence[str]
    ) -> BacktestResult:
        return run_backtest_once(config, start, end_exclusive, symbols, session_factory, fetch_bars)

    optimizer = OptunaOptimizer(
        run_backtest=run_trial_backtest,
        raw_config=raw_config,
        optimize_config=optimize_config,
        objective=objective,
        symbols=symbols,
        storage=build_worker_storage(storage_url),
        n_trials=n_trials,
        study_name=study_name,
        sampler_seed=sampler_seed,
    )
    try:
        optimizer.optimize()
    finally:
        db_engine.dispose()


def _run_optimize_parallel(
    config_path: Path,
    optimize_config_path: Path,
    raw_config: dict[str, object],
    optimize_config: OptimizeConfig,
    storage_url: str,
    n_trials: int,
    study_name: str,
    workers: int,
) -> None:
    # Fail fast in the parent instead of in every worker at once.
    validate_override_paths(raw_config, optimize_config.search_space)
    create_or_load_study(study_name, storage_url)

    base_seed = optimize_config.study.sampler_seed
    if base_seed is not None:
        logger.warning(
            "sampler_seed with workers > 1: each worker samples with seed+index "
            "and trials interleave nondeterministically — results are not "
            "reproducible run-to-run"
        )
    shares = [share for share in _split_trials(n_trials, workers) if share > 0]
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_optimize_worker,
            name=f"optimize-w{index}",
            args=(
                config_path,
                optimize_config_path,
                study_name,
                share,
                index,
                base_seed + index if base_seed is not None else None,
            ),
        )
        for index, share in enumerate(shares)
    ]
    logger.info(
        "study %r: running %d trials across %d workers %s",
        study_name,
        n_trials,
        len(processes),
        shares,
    )
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    failed = [process.name for process in processes if process.exitcode != 0]

    # The study is the source of truth: summarize whatever completed, even
    # when a worker crashed — the study is resumable by name.
    result = summarize_study(study_name, storage_url)
    write_optimize_summary(LOG_DIR, datetime.now(UTC), optimize_config.objective, result)
    _log_optimize_result(result, storage_url)
    if failed:
        raise SystemExit(
            f"worker(s) {', '.join(failed)} exited nonzero; the study is resumable — "
            f"rerun with --study-name {study_name} to continue"
        )


def _log_optimize_result(result: OptimizationResult, storage_url: str) -> None:
    logger.info(
        "study %r finished: %d complete (%d infeasible), %d failed",
        result.study_name,
        result.n_complete,
        result.n_infeasible,
        result.n_failed,
    )
    if result.best_trial_number is not None:
        logger.info(
            "best trial %d: score=%.4f run_id=%s params=%s",
            result.best_trial_number,
            result.best_score,
            result.best_run_id,
            result.best_params,
        )
        logger.info(
            "inspect the winning run in the 'Backtest Results' Grafana dashboard "
            "(run %s); browse the study: uvx --with 'psycopg[binary]' optuna-dashboard '%s'",
            result.best_run_id,
            storage_url,
        )


def _run_analyze(run_id: UUID) -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    db_engine = build_engine(settings.database_url)
    session_factory = build_session_factory(db_engine)
    try:
        run = SqlAlchemyRunRepository(session_factory).get(run_id)
        if run is None:
            raise SystemExit(f"no run with id {run_id}")
        # Recompute against the run's own effective config, not the current
        # config file — fitness and the summary reflect what the run ran with.
        config = AppConfig.model_validate(run.config) if run.config else load_config(CONFIG_PATH)
        metrics = BacktestAnalyzer(
            reader=SqlAlchemyAnalysisReader(session_factory),
            metrics_repo=SqlAlchemyBacktestMetricsRepository(session_factory),
            objectives=config.objectives,
            round_trips_repo=SqlAlchemyRoundTripRepository(session_factory),
        ).analyze(run_id)
        write_backtest_summary(
            LOG_DIR, run_id, datetime.now(UTC), _summary_regime(config), config.risk, metrics
        )
    finally:
        db_engine.dispose()


def _run_delete(run_id: UUID | None, all_backtests: bool) -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    db_engine = build_engine(settings.database_url)
    session_factory = build_session_factory(db_engine)
    try:
        runs_repo: RunRepository = SqlAlchemyRunRepository(session_factory)
        run_ids = runs_repo.list_run_ids(RunType.BACKTEST) if all_backtests else [run_id]
        deleted = 0
        for target in run_ids:
            assert target is not None  # guaranteed by the mutually exclusive CLI group
            if runs_repo.get(target) is None:
                logger.warning("no run with id %s, skipping", target)
                continue
            runs_repo.delete(target)  # data rows cascade
            logger.info("deleted run %s and all of its data", target)
            deleted += 1
        logger.info("deleted %d run(s)", deleted)
    finally:
        db_engine.dispose()


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}, expected YYYY-MM-DD") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="carcharoth", description="Algorithmic trading bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="live paper trading (default)")
    run.add_argument("--config", type=Path, default=CONFIG_PATH, help="config YAML path")

    backtest = subparsers.add_parser("backtest", help="replay historical data through the engine")
    backtest.add_argument("--start", type=_parse_date, required=True, help="YYYY-MM-DD (UTC)")
    backtest.add_argument("--end", type=_parse_date, required=True, help="YYYY-MM-DD, inclusive")
    backtest.add_argument(
        "--symbols", type=lambda s: s.split(","), default=None, help="comma-separated override"
    )
    backtest.add_argument("--config", type=Path, default=CONFIG_PATH, help="config YAML path")
    backtest.add_argument("--verbose", action="store_true", help="enable INFO console logging")

    optimize = subparsers.add_parser(
        "optimize", help="optimize config parameters over backtests (Optuna)"
    )
    optimize.add_argument(
        "--optimize-config",
        type=Path,
        default=Path("config/optimize.yaml"),
        help="study config YAML path",
    )
    optimize.add_argument("--config", type=Path, default=CONFIG_PATH, help="base config YAML path")
    optimize.add_argument("--n-trials", type=int, default=None, help="override study.n_trials")
    optimize.add_argument("--study-name", type=str, default=None, help="override study.name")
    optimize.add_argument(
        "--workers",
        type=int,
        default=None,
        help="override study.workers (parallel worker processes; trials are split across them)",
    )
    optimize.add_argument("--verbose", action="store_true", help="enable INFO console logging")

    analyze = subparsers.add_parser("analyze", help="recompute metrics for a run")
    analyze.add_argument("--run-id", type=UUID, required=True)

    delete = subparsers.add_parser("delete-run", help="delete a run and all of its data")
    target = delete.add_mutually_exclusive_group(required=True)
    target.add_argument("--run-id", type=UUID)
    target.add_argument("--all-backtests", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    import sys

    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        args_list = ["run"]  # plain `carcharoth` keeps starting the live bot
    args = _build_parser().parse_args(args_list)

    console_level = (
        "INFO" if args.command == "run" or getattr(args, "verbose", False) else "WARNING"
    )
    setup_logging(LOG_DIR, console_level=console_level)

    if args.command == "run":
        _run_live(args.config)
    elif args.command == "backtest":
        if args.end < args.start:
            raise SystemExit("--end must not be before --start")
        _run_backtest(args.config, args.start, args.end, args.symbols)
    elif args.command == "optimize":
        _run_optimize(
            args.config, args.optimize_config, args.n_trials, args.study_name, args.workers
        )
    elif args.command == "analyze":
        _run_analyze(args.run_id)
    elif args.command == "delete-run":
        _run_delete(args.run_id, args.all_backtests)


if __name__ == "__main__":
    main()
