"""Composition root: builds and wires all components.

This is the ONLY place that decides which concrete implementations run.
To swap a provider (e.g. another broker) or strategy, change the wiring
here — no other module needs to be touched.

CLI: `carcharoth` (or `carcharoth run`) starts live paper trading;
`carcharoth backtest` replays historical data through the same engine;
`carcharoth analyze` recomputes a backtest's metrics;
`carcharoth config` resolves/validates/diffs layered configs;
`carcharoth delete-run` removes a run and all of its data.

Every run command resolves a profile (default derived from the command)
through the layered config loader; `--set path=value` applies tracked
overrides on top.
"""

import argparse
import json
import logging
import multiprocessing
import signal
import time
import types
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import yaml
from pydantic import ValidationError

from carcharoth.analysis.analyzer import BacktestAnalyzer
from carcharoth.analysis.metrics import match_round_trips
from carcharoth.backtest.runner import BacktestRunner
from carcharoth.config.app_config import RegimeConfig, StrategyConfig
from carcharoth.config.loader import (
    CONFIG_DIR,
    ConfigError,
    ResolvedConfig,
    config_hash,
    load_profile,
    resolve_raw,
)
from carcharoth.config.optimize_config import OptimizeConfig
from carcharoth.config.overrides import validate_override_paths
from carcharoth.config.quicktest_config import PermutationConfig
from carcharoth.config.run_config import RunConfig, run_config_from_stored
from carcharoth.config.settings import Settings
from carcharoth.domain.models import BacktestResult, OptimizationResult, RunType
from carcharoth.engine.engine import TradingEngine
from carcharoth.engine.scheduler import Scheduler
from carcharoth.engine.strategy_provider import RegimeStrategyProvider, SingleStrategyProvider
from carcharoth.interfaces import BarsFetcher, ByteStore, StrategyProvider
from carcharoth.logging_setup import (
    setup_logging,
    write_backtest_summary,
    write_optimize_summary,
    write_permutation_summary,
    write_quicktest_summary,
)
from carcharoth.optimize.bars_cache import BarsCache
from carcharoth.permutation.registry import method_kind
from carcharoth.permutation.runner import run_monte_carlo_test, run_permutation_test
from carcharoth.persistence.buffered import (
    BufferedEquityOnlyRepository,
    BufferedPositionSnapshotRepository,
    BufferedRegimeEvaluationRepository,
    BufferedStrategyDecisionRepository,
    NoOpRegimeEvaluationRepository,
    NoOpStrategyDecisionRepository,
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
    SqlAlchemyPermutationRepository,
    SqlAlchemyPositionSnapshotRepository,
    SqlAlchemyRegimeEvaluationRepository,
    SqlAlchemyRoundTripRepository,
    SqlAlchemyRunRepository,
    SqlAlchemyStrategyAssignmentRepository,
    SqlAlchemyStrategyDecisionRepository,
    SqlAlchemyTradeRepository,
)
from carcharoth.quicktest.runner import run_quicktest_once
from carcharoth.regime.detectors import build_detector
from carcharoth.regime.hmm.fit_cache import HMM_PREFIX
from carcharoth.regime.models import Regime
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
from carcharoth.services.cache.bars import BARS_PREFIX, PersistentBarsCache
from carcharoth.services.cache.noop import NoOpCache
from carcharoth.services.cache.redis_store import build_redis_store
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
LOG_DIR = Path("logs")

#: default profile per run command (overridable with -p/--profile)
DEFAULT_PROFILES = {
    "run": "trading/paper",
    "backtest": "backtest",
    "quicktest": "quicktest",
    "optimize": "optimization",
}


def build_strategy_provider(
    config: RunConfig,
    session_factory: "sessionmaker[Session]",
    run_id: UUID,
    evaluations_repo: RegimeEvaluationRepository | None = None,
    hmm_store: ByteStore | None = None,
) -> StrategyProvider:
    if config.regime is None or not config.regime.active:
        name, sc = _active_strategy(config)  # exactly one, guaranteed by validation
        return SingleStrategyProvider(build_strategy(name, sc.params))
    return RegimeStrategyProvider(
        detector=build_detector(config.regime, hmm_fit_store=hmm_store),
        strategies={
            Regime(name): build_strategy(rc.strategy, config.strategies[rc.strategy].params)
            for name, rc in config.regime.regimes.items()
        },
        evaluations_repo=evaluations_repo
        or SqlAlchemyRegimeEvaluationRepository(session_factory, run_id),
        # Assignments are read back (load_current) and low-volume: never buffered.
        assignments_repo=SqlAlchemyStrategyAssignmentRepository(session_factory, run_id),
        evaluate_interval_minutes=config.regime.evaluate_interval_minutes,
        default_regime=Regime(config.regime.default_regime)
        if config.regime.default_regime
        else None,
        min_confidence=config.regime.hmm.min_confidence
        if config.regime.detector == "hmm" and config.regime.hmm is not None
        else None,
    )


def _active_strategy(config: RunConfig) -> tuple[str, StrategyConfig]:
    """The single active strategy for single-strategy mode; validation
    guarantees exactly one strategy has ``active: true``."""
    return next((name, sc) for name, sc in config.strategies.items() if sc.active)


def _build_cache_stores(
    settings: Settings, config: RunConfig, no_hmm_cache: bool = False
) -> tuple[ByteStore | None, ByteStore | None]:
    """(bars_store, hmm_store) per the cache config; None disables that cache.

    One Redis connection backs both; unreachable Redis (already warned about
    by build_redis_store) disables both and the run proceeds uncached.
    """
    want_bars = config.cache.enabled and config.cache.bars
    want_hmm = config.cache.enabled and config.cache.hmm and not no_hmm_cache
    if not (want_bars or want_hmm):
        return None, None
    store = build_redis_store(settings.redis_url)
    if store is None:
        return None, None
    return (store if want_bars else None), (store if want_hmm else None)


def _summary_regime(config: RunConfig) -> RegimeConfig | None:
    """The regime block to record in a backtest summary — only when it drove
    the run, so the summary reflects the mode that actually executed."""
    return config.regime if config.regime is not None and config.regime.active else None


def _summary_strategies(config: RunConfig) -> dict[str, Any]:
    """Strategy name → params dict for strategies that ran in this backtest."""
    if config.regime is not None and config.regime.active:
        names = {rc.strategy for rc in config.regime.regimes.values()}
    else:
        names = {_active_strategy(config)[0]}
    return {name: config.strategies[name].params for name in names if name in config.strategies}


def _strategy_description(config: RunConfig) -> str:
    if config.regime is not None and config.regime.active:
        return "regime-driven " + str(
            {name: rc.strategy for name, rc in config.regime.regimes.items()}
        )
    return _active_strategy(config)[0]


def _run_live(resolved: ResolvedConfig) -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    config = resolved.config

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
        symbols=config.symbols,
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
        symbols=config.symbols,
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
        "starting carcharoth: run_id=%s profile=%s config_hash=%s strategy=%s watchlist=%s",
        run_id,
        resolved.profile,
        resolved.hash,
        _strategy_description(config),
        config.symbols,
    )
    try:
        scheduler.run_forever()
    finally:
        runs_repo.finish(run_id, datetime.now(UTC))
        db_engine.dispose()
        logger.info("shutdown complete")


def run_backtest_once(
    config: RunConfig,
    start: datetime,
    end_exclusive: datetime,
    symbols: Sequence[str],
    session_factory: "sessionmaker[Session]",
    fetch_bars: BarsFetcher,
    show_progress: bool = False,
    hmm_store: ByteStore | None = None,
    verbose_db: bool = False,
    provenance: dict[str, Any] | None = None,
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

    # Slim mode (default) skips the three high-volume audit tables that grow
    # to GBs over long backtests or many optimization trials, while keeping
    # the equity curve, round trips, and metrics needed for analysis.
    if verbose_db:
        evaluations_repo = BufferedRegimeEvaluationRepository(buffer, run_id)
        decisions_repo = BufferedStrategyDecisionRepository(buffer, run_id)
        snapshots_repo = BufferedPositionSnapshotRepository(buffer, run_id)
    else:
        evaluations_repo = NoOpRegimeEvaluationRepository()
        decisions_repo = NoOpStrategyDecisionRepository()
        snapshots_repo = BufferedEquityOnlyRepository(buffer, run_id)

    # The provider dictates the bar timeframe and how much warm-up history
    # the first tick needs; the backtest prefetches accordingly.
    provider = build_strategy_provider(
        config,
        session_factory,
        run_id,
        evaluations_repo=evaluations_repo,
        hmm_store=hmm_store,
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
        decisions_repo=decisions_repo,
        orders_repo=orders_repo,
        trades_repo=trades_repo,
        snapshots_repo=snapshots_repo,
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
        show_progress=show_progress,
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
        LOG_DIR,
        run_id,
        started_at,
        _summary_regime(config),
        config.risk,
        _summary_strategies(config),
        metrics,
        # The hash covers the exact config this run executed with — for
        # optimizer trials that differs from the profile's resolved hash.
        config_hash=config_hash(config),
        provenance=provenance,
    )
    logger.info("backtest run %s complete", run_id)
    return BacktestResult(run_id=run_id, metrics=metrics)


def _backtest_permutation_config(config: RunConfig, method_override: str) -> PermutationConfig:
    """The `backtest.permutation:` section (defaulting to monte_carlo_trades
    when absent) with the CLI method override applied. Only trade-based
    methods can run against a finished backtest — bar methods would need to
    re-run the engine per permutation."""
    section = config.backtest.permutation or PermutationConfig(method="monte_carlo_trades")
    if method_override:
        section = PermutationConfig.model_validate(
            {**section.model_dump(), "method": method_override}
        )
    if method_kind(section.method) != "trades":
        raise SystemExit(
            f"permutation method {section.method!r} re-simulates permuted bars and is not "
            "supported for backtests; use a trade-based method (e.g. monte_carlo_trades) "
            "or run it via `carcharoth quicktest --permute`"
        )
    return section


def _run_backtest(
    resolved: ResolvedConfig,
    verbose: bool = False,
    no_hmm_cache: bool = False,
    verbose_db: bool = False,
    permute: str | None = None,
) -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    config = resolved.config
    watchlist = config.symbols
    start = config.data.start_dt
    end_exclusive = config.data.end_exclusive_dt
    # Validate before the (long) backtest so a bad method fails fast.
    permutation = _backtest_permutation_config(config, permute) if permute is not None else None

    db_engine = build_engine(settings.database_url)
    session_factory = build_session_factory(db_engine)
    bars_store, hmm_store = _build_cache_stores(settings, config, no_hmm_cache)
    fetch_bars: BarsFetcher = partial(fetch_historical_bars, build_data_client(settings))
    if bars_store is not None:
        fetch_bars = PersistentBarsCache(bars_store, fetch_bars)
    permutation_outcome = None
    try:
        # Only the interactive CLI run gets the tqdm bar; with --verbose we fall
        # back to the periodic progress logs to avoid clobbering the bar.
        result = run_backtest_once(
            config,
            start,
            end_exclusive,
            watchlist,
            session_factory,
            fetch_bars,
            show_progress=not verbose,
            hmm_store=hmm_store,
            verbose_db=verbose_db,
            provenance=resolved.stamp(),
        )
        if permutation is not None:
            # Monte carlo the finished run's closed trades — read back from
            # the DB, FIFO-matched exactly like the analyzer does.
            trades = SqlAlchemyAnalysisReader(session_factory).list_trades(result.run_id)
            round_trips = match_round_trips(trades)
            if not round_trips:
                logger.warning("backtest produced no closed round trips; skipping monte carlo")
            else:
                permutation_outcome = run_monte_carlo_test(
                    run_id=result.run_id,
                    round_trips=round_trips,
                    initial_capital=config.backtest.initial_capital,
                    permutation=permutation,
                    permutation_repo=SqlAlchemyPermutationRepository(session_factory),
                    flush=sqlalchemy_flush(session_factory),
                )
    finally:
        db_engine.dispose()

    if permutation_outcome is not None:
        write_permutation_summary(
            LOG_DIR,
            datetime.now(UTC),
            config,
            permutation_outcome,
            config_hash=resolved.hash,
            provenance=resolved.stamp(),
        )

    # Printed (not logged) so the essential result is always visible, even
    # without --verbose (which lowers the console log level to WARNING).
    print("backtest complete")
    print(f"  run_id:  {result.run_id}")
    print(f"  summary: {LOG_DIR / 'backtests' / f'{result.run_id}.yaml'}")
    if permutation_outcome is not None:
        print("monte carlo trade analysis complete")
        print(f"  test_id: {permutation_outcome.test_id}")
        print(f"  summary: {LOG_DIR / 'permutation' / f'{permutation_outcome.test_id}.yaml'}")


def _optimize_view_or_exit(config: RunConfig) -> OptimizeConfig:
    try:
        return config.optimize_view()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _run_optimize(resolved: ResolvedConfig, no_hmm_cache: bool = False) -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    config = resolved.config
    optimize_config = _optimize_view_or_exit(config)

    objective = config.objectives.get(optimize_config.objective)
    if objective is None:
        raise SystemExit(
            f"objective {optimize_config.objective!r} is not defined in profile "
            f"{resolved.profile!r} (available: {sorted(config.objectives)})"
        )
    symbols = config.symbols
    # Optuna gets its own schema in the shared Postgres (its internal
    # alembic_version table would otherwise collide with carcharoth's).
    storage_url = prepare_storage_url(settings.optuna_database_url or settings.database_url)

    if optimize_config.study.workers > 1:
        _run_optimize_parallel(
            resolved=resolved,
            optimize_config=optimize_config,
            storage_url=storage_url,
            no_hmm_cache=no_hmm_cache,
        )
        return

    db_engine = build_engine(settings.database_url)
    session_factory = build_session_factory(db_engine)
    # One data client + in-process bars cache for the whole study: trials
    # re-fetch nothing unless their warm-up window grows. The persistent
    # layer underneath carries bars (and HMM fits) across studies and runs.
    bars_store, hmm_store = _build_cache_stores(settings, config, no_hmm_cache)
    upstream: BarsFetcher = partial(fetch_historical_bars, build_data_client(settings))
    if bars_store is not None:
        upstream = PersistentBarsCache(bars_store, upstream)
    fetch_bars: BarsFetcher = BarsCache(upstream)

    def run_trial_backtest(
        config: RunConfig, start: datetime, end_exclusive: datetime, symbols: Sequence[str]
    ) -> BacktestResult:
        return run_backtest_once(
            config, start, end_exclusive, symbols, session_factory, fetch_bars, hmm_store=hmm_store
        )

    optimizer = OptunaOptimizer(
        run_backtest=run_trial_backtest,
        raw_config=resolved.raw,
        optimize_config=optimize_config,
        objective=objective,
        symbols=symbols,
        storage=storage_url,
    )
    try:
        result = optimizer.optimize()
    finally:
        db_engine.dispose()

    write_optimize_summary(
        LOG_DIR,
        datetime.now(UTC),
        optimize_config.objective,
        result,
        config_hash=resolved.hash,
        provenance=resolved.stamp(),
    )
    _log_optimize_result(result, storage_url)


def _split_trials(total: int, workers: int) -> list[int]:
    """Split a trial budget across workers; earlier workers take the remainder."""
    base, extra = divmod(total, workers)
    return [base + (1 if index < extra else 0) for index in range(workers)]


def _optimize_worker(
    profile: str,
    overrides: dict[str, Any],
    expected_hash: str,
    study_name: str,
    n_trials: int,
    worker_index: int,
    sampler_seed: int | None,
    no_hmm_cache: bool,
) -> None:
    """One parallel optimize worker (multiprocessing spawn target).

    Workers coordinate purely through Optuna's shared storage: each runs its
    share of trials with its own DB engines, Alpaca client, bars cache and
    log files. Each worker re-resolves the same (profile, overrides) the
    parent resolved and asserts the config hash matches — a mismatch means
    the config changed on disk mid-run. The parent writes the summary.
    """
    setup_logging(LOG_DIR, console_level="WARNING", filename_suffix=f".w{worker_index}")
    # Stagger startup so the workers' initial full-window bar fetches don't
    # hit Alpaca in the same instant (the bars cache is per-process).
    time.sleep(worker_index * 2.0)

    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    resolved = load_profile(profile, overrides)
    if resolved.hash != expected_hash:
        raise SystemExit(
            f"worker {worker_index}: resolved config hash {resolved.hash} does not match "
            f"the parent's {expected_hash} — config files changed since the study started"
        )
    config = resolved.config
    optimize_config = config.optimize_view()  # parent validated
    objective = config.objectives[optimize_config.objective]  # parent validated
    symbols = config.symbols
    storage_url = prepare_storage_url(settings.optuna_database_url or settings.database_url)

    db_engine = build_engine(settings.database_url, pool_size=2, max_overflow=2)
    session_factory = build_session_factory(db_engine)
    # Each worker connects to Redis itself (spawn context); the persistent
    # layer is what shares bars and HMM fits across the worker processes.
    bars_store, hmm_store = _build_cache_stores(settings, config, no_hmm_cache)
    upstream: BarsFetcher = partial(fetch_historical_bars, build_data_client(settings))
    if bars_store is not None:
        upstream = PersistentBarsCache(bars_store, upstream)
    fetch_bars: BarsFetcher = BarsCache(upstream)

    def run_trial_backtest(
        config: RunConfig, start: datetime, end_exclusive: datetime, symbols: Sequence[str]
    ) -> BacktestResult:
        return run_backtest_once(
            config, start, end_exclusive, symbols, session_factory, fetch_bars, hmm_store=hmm_store
        )

    optimizer = OptunaOptimizer(
        run_backtest=run_trial_backtest,
        raw_config=resolved.raw,
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
    resolved: ResolvedConfig,
    optimize_config: OptimizeConfig,
    storage_url: str,
    no_hmm_cache: bool = False,
) -> None:
    # Fail fast in the parent instead of in every worker at once.
    validate_override_paths(resolved.raw, optimize_config.search_space)
    n_trials = optimize_config.study.n_trials
    study_name = optimize_config.study.name
    workers = optimize_config.study.workers
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
                resolved.profile,
                resolved.overrides,
                resolved.hash,
                study_name,
                share,
                index,
                base_seed + index if base_seed is not None else None,
                no_hmm_cache,
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
    write_optimize_summary(
        LOG_DIR,
        datetime.now(UTC),
        optimize_config.objective,
        result,
        config_hash=resolved.hash,
        provenance=resolved.stamp(),
    )
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

    # Printed (not logged) so the essential result is always visible, even
    # without --verbose (which lowers the console log level to WARNING).
    print(f"study {result.study_name!r} finished")
    print(
        f"  trials:  {result.n_complete} complete "
        f"({result.n_infeasible} infeasible), {result.n_failed} failed"
    )
    if result.best_trial_number is not None:
        print(f"  best:    trial {result.best_trial_number}, score={result.best_score:.4f}")
        print(f"  run_id:  {result.best_run_id}")
        print(f"  summary: {LOG_DIR / 'optimize' / f'{result.study_name}.yaml'}")
        print("  inspect the winning run in the 'Backtest Results' Grafana dashboard")
        print(f"  browse the study: uvx --with 'psycopg[binary]' optuna-dashboard '{storage_url}'")


def _run_analyze(run_id: UUID) -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    db_engine = build_engine(settings.database_url)
    session_factory = build_session_factory(db_engine)
    try:
        run = SqlAlchemyRunRepository(session_factory).get(run_id)
        if run is None:
            raise SystemExit(f"no run with id {run_id}")
        # Recompute against the run's own effective config, not the current
        # config files — fitness and the summary reflect what the run ran with.
        if run.config:
            try:
                config = run_config_from_stored(run.config)
            except ValidationError as exc:
                raise SystemExit(
                    f"run {run_id} stored a config that is not analyzable as a run config "
                    f"(quicktest runs store only their quicktest section): {exc}"
                ) from exc
        else:
            config = load_profile(DEFAULT_PROFILES["backtest"]).config
        metrics = BacktestAnalyzer(
            reader=SqlAlchemyAnalysisReader(session_factory),
            metrics_repo=SqlAlchemyBacktestMetricsRepository(session_factory),
            objectives=config.objectives,
            round_trips_repo=SqlAlchemyRoundTripRepository(session_factory),
        ).analyze(run_id)
        write_backtest_summary(
            LOG_DIR,
            run_id,
            datetime.now(UTC),
            _summary_regime(config),
            config.risk,
            _summary_strategies(config),
            metrics,
            config_hash=config_hash(config),
        )
    finally:
        db_engine.dispose()


def _cache_store_or_exit() -> ByteStore:
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    store = build_redis_store(settings.redis_url, resilient=False)
    if store is None:
        raise SystemExit(
            f"redis unreachable at {settings.redis_url} — try `docker compose up -d redis`"
        )
    return store


def _run_cache_stats() -> None:
    store = _cache_store_or_exit()
    print(f"  bars entries:     {store.count_prefix(BARS_PREFIX)}")
    print(f"  hmm fit entries:  {store.count_prefix(HMM_PREFIX)}")
    used = store.used_memory_bytes()
    if used is not None:
        print(f"  redis memory:     {used / 1_048_576:.1f} MiB")


def _run_cache_clear(bars: bool, hmm: bool) -> None:
    store = _cache_store_or_exit()
    both = not bars and not hmm
    if bars or both:
        print(f"  deleted {store.delete_prefix(BARS_PREFIX)} bars entries")
    if hmm or both:
        print(f"  deleted {store.delete_prefix(HMM_PREFIX)} hmm fit entries")


def _run_delete(run_id: UUID | None, all_backtests: bool, all_quicktests: bool = False) -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    db_engine = build_engine(settings.database_url)
    session_factory = build_session_factory(db_engine)
    try:
        runs_repo: RunRepository = SqlAlchemyRunRepository(session_factory)
        if all_backtests:
            run_ids = runs_repo.list_run_ids(RunType.BACKTEST)
        elif all_quicktests:
            run_ids = runs_repo.list_run_ids(RunType.QUICKTEST)
        else:
            run_ids = [run_id]
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


def _effective_permutation_config(
    config_section: PermutationConfig | None, method_override: str, workers: int | None
) -> PermutationConfig:
    """The `permutation:` YAML section (or defaults when absent) with CLI
    overrides applied: `--permute METHOD` picks the method, `--workers N` the
    process count. Overrides are validated like the YAML section."""
    section = config_section or PermutationConfig()
    updates: dict[str, Any] = {}
    if method_override:
        updates["method"] = method_override
    if workers is not None:
        updates["workers"] = workers
    if not updates:
        return section
    return PermutationConfig.model_validate({**section.model_dump(), **updates})


def _run_quicktest(
    resolved: ResolvedConfig,
    permute: str | None = None,
    workers: int | None = None,
) -> None:
    """Isolated strategy quick test: no engine, no regime, no risk manager.

    The quicktest view of the resolved config supplies symbols, window and
    the strategy (params from the shared `strategies` map). With `--permute`
    the quicktest becomes the baseline of a permutation test.
    """
    settings = Settings()  # type: ignore[call-arg]  # values come from .env
    base_config = resolved.config
    try:
        config = base_config.quicktest_view()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if config.objective not in base_config.objectives:
        logger.warning(
            "objective %r not defined in profile %r objectives: %s",
            config.objective,
            resolved.profile,
            sorted(base_config.objectives),
        )

    db_engine = build_engine(settings.database_url)
    session_factory = build_session_factory(db_engine)
    bars_store, _ = _build_cache_stores(settings, base_config)
    fetch_bars: BarsFetcher = partial(fetch_historical_bars, build_data_client(settings))
    if bars_store is not None:
        fetch_bars = PersistentBarsCache(bars_store, fetch_bars)
    started_at = datetime.now(UTC)
    permutation_outcome = None
    try:
        permutation = (
            _effective_permutation_config(config.permutation, permute, workers)
            if permute is not None
            else None
        )
        if permutation is None or method_kind(permutation.method) == "trades":
            outcome = run_quicktest_once(
                config,
                base_config.objectives,
                fetch_bars,
                runs_repo=SqlAlchemyRunRepository(session_factory),
                flush=sqlalchemy_flush(session_factory),
                round_trips_repo=SqlAlchemyRoundTripRepository(session_factory),
                metrics_repo=SqlAlchemyBacktestMetricsRepository(session_factory),
            )
            if permutation is not None:
                # Trade-shuffle methods need no re-simulation: monte-carlo the
                # baseline quicktest's round trips in-process.
                permutation_outcome = run_monte_carlo_test(
                    run_id=outcome.run_id,
                    round_trips=outcome.round_trips,
                    initial_capital=config.capital * len(config.symbols),
                    permutation=permutation,
                    permutation_repo=SqlAlchemyPermutationRepository(session_factory),
                    flush=sqlalchemy_flush(session_factory),
                )
        else:
            outcome, permutation_outcome = run_permutation_test(
                config,
                permutation,
                base_config.objectives,
                fetch_bars,
                runs_repo=SqlAlchemyRunRepository(session_factory),
                flush=sqlalchemy_flush(session_factory),
                round_trips_repo=SqlAlchemyRoundTripRepository(session_factory),
                metrics_repo=SqlAlchemyBacktestMetricsRepository(session_factory),
                permutation_repo=SqlAlchemyPermutationRepository(session_factory),
            )
    finally:
        db_engine.dispose()

    write_quicktest_summary(
        LOG_DIR,
        outcome.run_id,
        started_at,
        config,
        outcome.metrics,
        config_hash=resolved.hash,
        provenance=resolved.stamp(),
    )
    if permutation_outcome is not None:
        write_permutation_summary(
            LOG_DIR,
            started_at,
            config,
            permutation_outcome,
            config_hash=resolved.hash,
            provenance=resolved.stamp(),
        )

    # Printed (not logged) so the essential result is always visible, even
    # without --verbose (which lowers the console log level to WARNING).
    print("quicktest complete")
    print(f"  run_id:  {outcome.run_id}")
    print(f"  summary: {LOG_DIR / 'quicktest' / f'{outcome.run_id}.yaml'}")
    if permutation_outcome is not None:
        print("permutation test complete")
        print(f"  test_id: {permutation_outcome.test_id}")
        print(f"  summary: {LOG_DIR / 'permutation' / f'{permutation_outcome.test_id}.yaml'}")


def _parse_set(entries: Sequence[str] | None) -> dict[str, Any]:
    """``--set path=value`` entries -> overrides dict; values are YAML-parsed
    (``12`` -> int, ``true`` -> bool, ``[A,B]`` -> list)."""
    overrides: dict[str, Any] = {}
    for entry in entries or []:
        path, sep, value = entry.partition("=")
        if not sep or not path:
            raise SystemExit(f"invalid --set {entry!r}, expected PATH=VALUE")
        try:
            overrides[path] = yaml.safe_load(value)
        except yaml.YAMLError as exc:
            raise SystemExit(f"invalid --set value {entry!r}: {exc}") from exc
    return overrides


def _resolve_or_exit(profile: str, overrides: Mapping[str, Any]) -> ResolvedConfig:
    try:
        return load_profile(profile, overrides)
    except ConfigError as exc:
        raise SystemExit(f"config error: {exc}") from exc
    except ValidationError as exc:
        raise SystemExit(f"invalid config for profile {profile!r}:\n{exc}") from exc


def _run_config_list() -> None:
    def names(subdir: str) -> list[str]:
        return sorted(p.stem for p in (CONFIG_DIR / subdir).glob("*.yaml"))

    print("profiles:      " + ", ".join(names("profiles")))
    print("trading:       " + ", ".join(f"trading/{n}" for n in names("trading")))
    print("symbols:       " + ", ".join(f"symbols/{n}" for n in names("symbols")))
    print("strategies:    " + ", ".join(f"strategies/{n}" for n in names("strategies")))
    print("search spaces: " + ", ".join(f"optimization/{n}" for n in names("optimization")))


def _run_config_resolve(profile: str, overrides: Mapping[str, Any], fmt: str) -> None:
    resolved = _resolve_or_exit(profile, overrides)
    dump = resolved.config.model_dump(mode="json")
    if fmt == "json":
        print(json.dumps({"config_hash": resolved.hash, "config": dump}, indent=2))
    else:
        print(f"# profile: {profile}  config_hash: {resolved.hash}")
        print(yaml.dump(dump, default_flow_style=False, sort_keys=False, allow_unicode=True))


def _run_config_validate(profile: str, overrides: Mapping[str, Any], as_json: bool) -> None:
    """Exit 0 with the hash when valid; exit 1 with structured errors when not."""
    try:
        resolved = load_profile(profile, overrides)
    except ConfigError as exc:
        _print_validation_failure(profile, [{"path": "", "message": str(exc)}], as_json)
        raise SystemExit(1) from exc
    except ValidationError as exc:
        errors = [
            {"path": ".".join(str(loc) for loc in e["loc"]), "message": e["msg"]}
            for e in exc.errors()
        ]
        _print_validation_failure(profile, errors, as_json)
        raise SystemExit(1) from exc
    if as_json:
        print(
            json.dumps(
                {
                    "valid": True,
                    "profile": profile,
                    "config_hash": resolved.hash,
                    "layers": resolved.layers,
                }
            )
        )
    else:
        print(f"valid: profile={profile} config_hash={resolved.hash}")


def _print_validation_failure(profile: str, errors: list[dict[str, str]], as_json: bool) -> None:
    if as_json:
        print(json.dumps({"valid": False, "profile": profile, "errors": errors}))
    else:
        print(f"INVALID: profile={profile}")
        for error in errors:
            prefix = f"  {error['path']}: " if error["path"] else "  "
            print(f"{prefix}{error['message']}")


def _flatten(node: Any, prefix: str = "") -> dict[str, Any]:
    """Nested dicts -> ``{dot.path: leaf}`` (lists and scalars are leaves)."""
    if isinstance(node, dict) and node:
        flat: dict[str, Any] = {}
        for key, value in node.items():
            flat.update(_flatten(value, f"{prefix}{key}."))
        return flat
    return {prefix[:-1]: node}


def _run_config_diff(profile: str, against: str, overrides: Mapping[str, Any]) -> None:
    """Leaf-level diff of two merged (pre-validation) configs; ``against``
    defaults to the bare base layer."""
    try:
        left_raw, _, _ = resolve_raw(against)
        right_raw, _, _ = resolve_raw(profile, overrides)
    except ConfigError as exc:
        raise SystemExit(f"config error: {exc}") from exc
    left, right = _flatten(left_raw), _flatten(right_raw)
    for path in sorted(set(left) | set(right)):
        if path not in left:
            print(f"+ {path} = {right[path]}")
        elif path not in right:
            print(f"- {path} = {left[path]}")
        elif left[path] != right[path]:
            print(f"~ {path}: {left[path]} -> {right[path]}")


def _add_profile_args(parser: argparse.ArgumentParser, default_profile: str) -> None:
    parser.add_argument(
        "-p",
        "--profile",
        type=str,
        default=None,
        help=f"config profile (default: {default_profile}); "
        "a name in config/profiles/ or a config-root-relative path like trading/paper",
    )
    parser.add_argument(
        "--set",
        action="append",
        dest="set",
        metavar="PATH=VALUE",
        help="override a config value by dot-path (repeatable), "
        "e.g. --set strategies.mean_reversion.params.entry_z=-1.5",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="carcharoth", description="Algorithmic trading bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="live paper trading (default)")
    _add_profile_args(run, DEFAULT_PROFILES["run"])

    backtest = subparsers.add_parser("backtest", help="replay historical data through the engine")
    _add_profile_args(backtest, DEFAULT_PROFILES["backtest"])
    backtest.add_argument(
        "--start",
        type=_parse_date,
        default=None,
        help="YYYY-MM-DD (UTC); overrides data.start",
    )
    backtest.add_argument(
        "--end",
        type=_parse_date,
        default=None,
        help="YYYY-MM-DD, inclusive; overrides data.end",
    )
    backtest.add_argument(
        "--symbols",
        type=lambda s: s.split(","),
        default=None,
        help="comma-separated override of the symbol universe",
    )
    backtest.add_argument("--verbose", action="store_true", help="enable INFO console logging")
    backtest.add_argument(
        "--no-hmm-cache",
        action="store_true",
        help="disable the persistent HMM fit cache for this run",
    )
    backtest.add_argument(
        "--verbose-db",
        action="store_true",
        help=(
            "persist strategy decisions, position snapshots, and regime evaluations; "
            "default skips these high-volume tables to keep DB size small"
        ),
    )
    backtest.add_argument(
        "--permute",
        nargs="?",
        const="",
        default=None,
        metavar="METHOD",
        help="monte carlo the finished backtest's trades; METHOD overrides "
        "backtest.permutation.method (trade-based methods only, e.g. monte_carlo_trades)",
    )

    optimize = subparsers.add_parser(
        "optimize", help="optimize config parameters over backtests (Optuna)"
    )
    _add_profile_args(optimize, DEFAULT_PROFILES["optimize"])
    optimize.add_argument(
        "--n-trials", type=int, default=None, help="override optimization.study.n_trials"
    )
    optimize.add_argument(
        "--study-name", type=str, default=None, help="override optimization.study.name"
    )
    optimize.add_argument(
        "--workers",
        type=int,
        default=None,
        help="override optimization.study.workers (parallel worker processes)",
    )
    optimize.add_argument("--verbose", action="store_true", help="enable INFO console logging")
    optimize.add_argument(
        "--no-hmm-cache",
        action="store_true",
        help="disable the persistent HMM fit cache (use when the study searches HMM params)",
    )

    cache = subparsers.add_parser("cache", help="inspect or clear the persistent Redis cache")
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    cache_sub.add_parser("stats", help="entry counts per cache + redis memory usage")
    clear = cache_sub.add_parser("clear", help="delete cached entries (both caches by default)")
    clear.add_argument("--bars", action="store_true", help="clear only the bars cache")
    clear.add_argument("--hmm", action="store_true", help="clear only the HMM fit cache")

    quicktest = subparsers.add_parser(
        "quicktest", help="isolated strategy quick test (no engine, no regime, no risk)"
    )
    _add_profile_args(quicktest, DEFAULT_PROFILES["quicktest"])
    quicktest.add_argument("--verbose", action="store_true", help="enable INFO console logging")
    quicktest.add_argument(
        "--permute",
        nargs="?",
        const="",
        default=None,
        metavar="METHOD",
        help="run a permutation test around the quicktest; METHOD overrides "
        "the config's permutation.method (see permutation/registry.py)",
    )
    quicktest.add_argument(
        "--workers",
        type=int,
        default=None,
        help="permutation worker processes (overrides permutation.workers; 0 = auto)",
    )

    config_cmd = subparsers.add_parser("config", help="inspect, validate, and diff layered configs")
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("list", help="available profiles, symbol sets, presets, search spaces")
    resolve = config_sub.add_parser("resolve", help="print the fully merged, validated config")
    resolve.add_argument("-p", "--profile", type=str, required=True)
    resolve.add_argument("--set", action="append", dest="set", metavar="PATH=VALUE")
    resolve.add_argument("--format", choices=["yaml", "json"], default="yaml")
    validate = config_sub.add_parser("validate", help="validate a profile (exit 1 when invalid)")
    validate.add_argument("-p", "--profile", type=str, required=True)
    validate.add_argument("--set", action="append", dest="set", metavar="PATH=VALUE")
    validate.add_argument("--json", action="store_true", help="machine-readable output")
    hash_cmd = config_sub.add_parser("hash", help="print the resolved config's content hash")
    hash_cmd.add_argument("-p", "--profile", type=str, required=True)
    hash_cmd.add_argument("--set", action="append", dest="set", metavar="PATH=VALUE")
    diff = config_sub.add_parser("diff", help="leaf-level diff between two merged configs")
    diff.add_argument("-p", "--profile", type=str, required=True)
    diff.add_argument("--against", type=str, default="base", help="profile or layer to diff from")
    diff.add_argument("--set", action="append", dest="set", metavar="PATH=VALUE")
    config_sub.add_parser("schema", help="print the config JSON Schema (for tooling/TUIs)")

    analyze = subparsers.add_parser("analyze", help="recompute metrics for a run")
    analyze.add_argument("--run-id", type=UUID, required=True)

    delete = subparsers.add_parser("delete-run", help="delete a run and all of its data")
    target = delete.add_mutually_exclusive_group(required=True)
    target.add_argument("--run-id", type=UUID)
    target.add_argument("--all-backtests", action="store_true")
    target.add_argument("--all-quicktests", action="store_true")

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

    if args.command == "config":
        overrides = _parse_set(getattr(args, "set", None))
        if args.config_command == "list":
            _run_config_list()
        elif args.config_command == "resolve":
            _run_config_resolve(args.profile, overrides, args.format)
        elif args.config_command == "validate":
            _run_config_validate(args.profile, overrides, args.json)
        elif args.config_command == "hash":
            print(_resolve_or_exit(args.profile, overrides).hash)
        elif args.config_command == "diff":
            _run_config_diff(args.profile, args.against, overrides)
        elif args.config_command == "schema":
            print(json.dumps(RunConfig.model_json_schema(), indent=2))
        return

    if args.command in DEFAULT_PROFILES:
        profile = args.profile or DEFAULT_PROFILES[args.command]
        overrides = _parse_set(args.set)
        # CLI sugar flags are implemented as overrides so they show up in
        # the run's provenance and are exactly replayable.
        if args.command == "backtest":
            if args.start is not None:
                overrides["data.start"] = args.start.date()
            if args.end is not None:
                overrides["data.end"] = args.end.date()
            if args.symbols is not None:
                overrides["symbols"] = args.symbols
        elif args.command == "optimize":
            if args.n_trials is not None:
                overrides["optimization.study.n_trials"] = args.n_trials
            if args.study_name is not None:
                overrides["optimization.study.name"] = args.study_name
            if args.workers is not None:
                overrides["optimization.study.workers"] = args.workers
        resolved = _resolve_or_exit(profile, overrides)

    if args.command == "run":
        _run_live(resolved)
    elif args.command == "backtest":
        _run_backtest(
            resolved,
            verbose=args.verbose,
            no_hmm_cache=args.no_hmm_cache,
            verbose_db=args.verbose_db,
            permute=args.permute,
        )
    elif args.command == "optimize":
        _run_optimize(resolved, no_hmm_cache=args.no_hmm_cache)
    elif args.command == "quicktest":
        _run_quicktest(resolved, args.permute, args.workers)
    elif args.command == "cache":
        if args.cache_command == "stats":
            _run_cache_stats()
        else:
            _run_cache_clear(args.bars, args.hmm)
    elif args.command == "analyze":
        _run_analyze(args.run_id)
    elif args.command == "delete-run":
        _run_delete(args.run_id, args.all_backtests, args.all_quicktests)


if __name__ == "__main__":
    main()
