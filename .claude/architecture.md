# Carcharoth Architecture Overview

Carcharoth is a **composable, interface-first** trading system. Every component is swappable;
new implementations never require changes to existing code.

For design principles and day-to-day workflows, see [CLAUDE.md](CLAUDE.md) and its linked docs.

## High-Level Flow

```
Scheduler (every minute, market hours only)
   │
   └──> TradingEngine.tick()
        ├─ 0. Order Executor   — check fills, update DB → trades.log
        ├─ 1. Market Data      — fetch bars + quotes (Alpaca)
        ├─ 2. Account Service  — fetch equity, buying power, positions (Alpaca)
        ├─ 3. Strategy Engine  — generate BUY/SELL/HOLD signal (strategy-specific)
        ├─ 4. Risk Manager     — approve/reject, size position
        └─ 5. Order Executor   — submit approved orders (Alpaca)
```

Each step passes **typed domain objects** between components. The engine itself is "stupid"
(it just orchestrates); all logic lives inside the components.

### Backtesting Flow

The same `TradingEngine` replays historical data — no engine changes, only different wiring:

```
carcharoth backtest --start ... --end ...
   │
   ├─ fetch_historical_bars()          — one-shot Alpaca fetch incl. warm-up window,
   │    behind PersistentBarsCache (Redis, services/cache/bars.py): per-symbol coverage
   │    windows are gap-filled, so repeat runs over the same window fetch nothing
   └──> BacktestRunner (no scheduler; one tick per historical bar, as fast as possible)
        ├─ market_data.advance_to(ts)          — move the simulated "now"
        ├─ broker.mark_to_market(ts, closes)   — reprice positions, roll last_equity per session
        └─ TradingEngine.tick()                — unchanged live tick sequence
   after the loop: final reconcile pass, then BacktestAnalyzer computes + persists metrics
```

Backtest-specific components (wired in `main.py` instead of the Alpaca services):
- `HistoricalMarketDataService` (implements `MarketDataService`) — preloaded bars behind a
  movable cursor; synthesizes quotes from the newest bar close ± half the configured spread
- `SimulatedBroker` (implements `AccountService` + `OrderExecutor`) — tracks cash/positions
  from a configurable initial capital; market orders fill instantly at close ± spread/slippage.
  **Fill contract:** `submit()` returns ACCEPTED (while filling internally); `get_order()`
  reports FILLED — so the engine's normal reconcile step records the trade, exactly as live.

CLI commands, fitness scoring, write buffering, and cache behaviour: [backtest.md](backtest.md).

### Optimization Flow

`carcharoth optimize` wraps the unchanged backtest flow in an Optuna study:

```
carcharoth optimize
   │
   ├─ load config.yaml (raw + validated) and optimize.yaml (search space, budget, window)
   └──> OptunaOptimizer (services/optuna/) — the only optuna-importing package
        per trial:
        ├─ suggest values for each search-space dot-path (e.g. risk.max_position_pct_equity)
        ├─ apply_overrides() on the raw config dict → AppConfig.model_validate()
        │    (invalid combinations FAIL the trial; the study continues)
        ├─ BacktestFunc → run_backtest_once()   — a normal, fully persisted backtest run
        ├─ trial.user_attr run_id = <run's id>  — linkage lives on the Optuna side only
        └─ objective value = the run's fitness_<objective> metric
             (constraint violations return the penalty score instead — search guidance
              only; the run's persisted fitness is untouched)
   after the study: study summary → logs/optimize/<study_name>.yaml
```

Key properties:
- **Runs stay independent**: a trial's backtest run is indistinguishable from a manual one;
  the backtest side knows nothing about Optuna.
- **Fitness is an analysis output, not an optimizer computation**: every backtest scores
  itself against each named objective in `config.yaml` (`objectives:`) and persists
  `fitness_<name>` to `backtest_metrics`. The optimizer just reads it.
- **Config-driven search space**: `config/optimize.yaml` maps dot-paths to distributions
  (int/float/categorical); changing what gets optimized requires no code change.
- **Bars cache**: one in-process union-window cache (`optimize/bars_cache.py`) serves all
  trials; the warm-up prefix varies per trial, so the cache widens instead of re-fetching.
  It sits on top of the persistent Redis bars cache (`services/cache/bars.py`), which
  carries bars across studies, runs and `--workers N` processes.
- **HMM fit cache**: fitted HMM models are cached in Redis keyed by (config hash, symbol,
  exact training input) — trials with unchanged HMM config skip every refit. Disable with
  `cache.hmm: false` or `--no-hmm-cache` when the study searches HMM params (each trial
  would get a fresh config hash and never hit).

CLI commands, resumable studies, parallel workers, and constraints: [optimize.md](optimize.md).
Optuna storage and database tables: [development practices](principles/development.md#database--migrations).

### Run Tracking

Every start creates a row in `runs` (`run_type` PAPER for live sessions, BACKTEST for
backtests). All data repositories are **run-scoped**: constructed with the `run_id`, they
read and write only that run's rows. This isolates backtests from live data and from each
other, and `carcharoth delete-run` removes a run with all its data via FK cascade.
Trade-offs: a restarted live process does not reconcile the previous run's open orders
(negligible — DAY market orders fill in seconds) and regime assignments start fresh per run.

---

## Folder Structure & Responsibilities

### `src/carcharoth/main.py`
**Composition Root** — the only place that wires concrete implementations.

- Builds the Alpaca clients, strategy, risk manager, repos, etc.
- Decides which broker, strategy, and risk policy run
- Injects everything into the engine and scheduler
- To swap a provider (e.g., use IB instead of Alpaca): edit here once, nowhere else changes

### `src/carcharoth/interfaces/`
**Component Contracts** (Abstract Base Classes)

Each module defines one abstract class that a concrete implementation must extend:
- `account.py` → `AccountService` — fetch equity, buying power, positions
- `market_data.py` → `MarketDataService` — fetch bars and quotes
- `execution.py` → `OrderExecutor` — submit orders and query fills
- `clock.py` → `MarketClock` — check if market is open
- `strategy.py` → `Strategy` — evaluate signal for a symbol
- `strategy_provider.py` → `StrategyProvider` — provide strategy (single or regime-based)
- `regime_detector.py` → `RegimeDetector` — classify a symbol's market regime from bars
  (implementations may keep per-symbol state, e.g. fitted models; one detector per run)
- `risk.py` → `RiskManager` — approve/reject orders and size positions
- `cache.py` → `Cache` — optional read-through cache slot (live services) — and
  `ByteStore` — persistent bytes key/value store backing the cross-run caches
  (Redis in production, an in-memory dict in tests)
- `optimization.py` → `BarsFetcher` / `BacktestFunc` (Protocols) + `ParameterOptimizer` (ABC)
  — the optimizer consumes a backtest as a callable; swapping the optimization library
  touches only `services/optuna/`
- `repository.py` — SQLAlchemy repository ABCs for persistence

**Why:** Tests inject fakes; real code uses Alpaca. Swapping providers means 4 interface
implementations + one change in `main.py`.

### `src/carcharoth/engine/`
**Orchestration & Scheduling**

- `engine.py` — `TradingEngine.tick()` — the fixed 5-step sequence (defined in README)
- `scheduler.py` — `Scheduler` — runs the engine every minute during market hours
- `strategy_provider.py` — `SingleStrategyProvider` and `RegimeStrategyProvider` — wraps
  single/regime-based strategy selection

The engine does not know about Alpaca, databases, or strategy details; it just calls
interfaces in order.

### `src/carcharoth/services/`
**Concrete Provider Implementations**

- `alpaca/` — Alpaca-specific code
  - `account.py` → `AlpacaAccountService` (implements `AccountService`)
  - `market_data.py` → `AlpacaMarketDataService` (implements `MarketDataService`)
  - `execution.py` → `AlpacaOrderExecutor` (implements `OrderExecutor`)
  - `clock.py` → `AlpacaMarketClock` (implements `MarketClock`)
  - `historical.py` — one-shot historical bar fetch + shared warm-up window heuristics
  - `mappers.py` — converts Alpaca SDK types → domain models (type boundary crossing here)
- `backtest/` — simulation implementations used by `carcharoth backtest`
  - `market_data.py` → `HistoricalMarketDataService` (implements `MarketDataService`)
  - `broker.py` → `SimulatedBroker` (implements `AccountService` + `OrderExecutor`)
- `cache/` — caching implementations
  - `noop.py` → `NoOpCache` — no-op cache (the live path's default)
  - `redis_store.py` → `RedisByteStore` + `build_redis_store()` — the only module that
    imports `redis`; unreachable Redis degrades to no caching with one warning
  - `resilient.py` → `ResilientByteStore` — wraps the store so a Redis that dies mid-run
    short-circuits permanently instead of timing out on every call
  - `bars.py` → `PersistentBarsCache` (implements `BarsFetcher`) — per-(timeframe, symbol)
    coverage windows in Redis, gap-filled; coverage never extends into the current UTC day,
    so partial intraday data is never durably cached. Keys: `carch:bars:v1:{tf}:{symbol}`
- `optuna/` — Optuna adapter (the only package that imports optuna)
  - `optimizer.py` → `OptunaOptimizer` (implements `ParameterOptimizer`)
  - `search_space.py` — config-driven search space → `trial.suggest_*` mapping

All Alpaca SDK types stay inside `alpaca/`; external code works with domain models.

### `src/carcharoth/backtest/`
**Backtest Orchestration**

- `runner.py` — `BacktestRunner` — iterates the historical bar grid (regular session bars
  only, mirroring the live scheduler) and calls `engine.tick()` per bar; one tick == one bar,
  and time-based settings (the regime detectors' `evaluate_interval_minutes`) follow the bar
  timestamps, so they mean the same market time as in live trading

### `src/carcharoth/analysis/`
**Post-Run Analysis**

- `metrics.py` — pure metric computations: FIFO round-trip matching, total return, max
  drawdown, annualized Sharpe, win rate, profit factor, per-symbol PnL
- `objective.py` — pure fitness scoring: weighted composite over metrics per named
  objective (`objectives:` in config.yaml); persisted as `fitness_<name>` per run
- `analyzer.py` — `BacktestAnalyzer` — reads a run's persisted trades/equity, computes
  metrics (incl. fitness) and persists them to `backtest_metrics` (runs automatically after
  each backtest; `carcharoth analyze --run-id X` recomputes against the run's own config)

### `src/carcharoth/optimize/`
**Optimization Support Logic** (pure, optimizer-library-agnostic)

- `overrides.py` — dot-path overrides on the raw config dict; strict paths so a typo'd
  search-space entry fails fast instead of silently optimizing a dead parameter
- `constraints.py` — hard-constraint checks on trial metrics (optimizer-side only)
- `bars_cache.py` — `BarsCache` (implements `BarsFetcher`) — in-process union-window cache
  of historical bars across trials

### `src/carcharoth/strategies/`
**Trading Strategies**

Each strategy implements `Strategy.evaluate(market_data, account, positions) → Signal` (pure — no I/O).

- `mean_reversion.py` — mean reversion strategy (uses moving averages, z-scores)
- `ema_vwap.py` — EMA + VWAP combination strategy
- `registry.py` — registry: `{"mean_reversion": MeanReversionStrategy, ...}`
- `signals.py` — `Signal` enum (BUY, SELL, HOLD)

New strategies register here and are selected in `config/config.yaml`.

### `src/carcharoth/regime/`
**Market Regime Detection**

Dynamically switches between strategies based on market conditions. Two detector
implementations exist behind the `RegimeDetector` ABC (`interfaces/regime_detector.py`),
selected via `regime.detector` in `config/config.yaml`:

- `score_detector.py` — `ScoreRegimeDetector` — evidence-based: weighted feature scores on a
  trend ↔ mean-reversion axis, attenuated by stability (change-detection) evidence.
  Emits `trending` / `mean_reverting`.
- `hmm/` — `HmmRegimeDetector` — a per-symbol Gaussian hidden Markov model (hmmlearn) over
  `[log return, rolling log-volatility, EMA-distance, ADX]`, lazily fitted on
  `training_window` bars and refit after `refit_interval_bars` new bars. Hidden states are
  labeled from their emission means (`hmm/labeling.py`); the assessment is the posterior of
  the newest bar and carries the full probability distribution. Emits `trending_up` /
  `trending_down` / `range_bound` / `high_volatility`.
  - `features.py` — observation-matrix builder (TA-Lib/numpy, NaN warm-up rows dropped)
  - `labeling.py` — deterministic state → regime labeling from emission means
  - `detector.py` — fit/refit/inference, seeded per symbol for reproducible backtests
  - `fit_cache.py` — `HmmFitCache` — optional persistent cache of fitted models (injected
    via `build_detector(..., hmm_fit_store=...)`); fitting is seed-deterministic, so a
    cached fit is bit-identical to a fresh one. Keys:
    `carch:hmm:v1:{config_hash}:{symbol}:{obs_hash}` — the config hash covers every
    fit-relevant field plus the hmmlearn version; the observation hash covers the exact
    training matrix
- `models.py` — `Regime` enum (all six values above), `Evidence`, `RegimeAssessment`
  (incl. optional `probabilities`), `StrategyAssignment`
- `features/` — score-detector feature implementations (hurst, cusum, ...)
- `registry.py` — feature registry for the score detector
- `detectors.py` — `build_detector(RegimeConfig)` — config → concrete detector (the
  detector counterpart to `strategies/registry.py`)

When enabled in config, `RegimeStrategyProvider` re-assesses each symbol's regime on the
selected detector's `evaluate_interval_minutes` (market time — identical live and in
backtests), persists the evaluation, and switches the symbol's strategy hold-until-flat.
Regimes without a mapped strategy don't trade; probabilistic assessments below
`min_confidence` keep the previous regime (the distribution is persisted either way).

Config reference for detectors and regime mappings:
[development practices](principles/development.md#configuration).

### `src/carcharoth/domain/`
**Pure Domain Models**

- `models.py` — core types: `Signal`, `Order`, `Position`, `Trade`, `Bar`, `Quote`, `Account`

These are simple, type-hinted dataclasses with no I/O. They flow between all layers.

### `src/carcharoth/risk/`
**Risk Management**

- `basic.py` → `BasicRiskManager` (implements `RiskManager`)
  - `approve_orders()` — filter orders by portfolio constraints
  - `size_position()` — calculate position size (fixed %, Kelly criterion, etc.)

Override this to add custom risk logic (volatility-based sizing, drawdown limits, etc.).

### `src/carcharoth/persistence/`
**Data Access Layer**

- `orm.py` — SQLAlchemy schema (tables: `runs`, `trades`, `orders`, `positions_snapshot`,
  `strategy_decisions`, `configurations`, `regime_evaluations`, `strategy_assignments`,
  `equity_snapshots`, `backtest_metrics`)
- `repositories.py` — Repository ABCs + SQLAlchemy implementations (`SqlAlchemy*Repository`);
  data repositories are run-scoped (constructed with a `run_id`)
- `db.py` — session factory and connection setup
- `buffered.py` — bulk-insert buffering for high-volume backtest rows (wired only on the
  backtest path)

All reads/writes to Postgres go through repositories; this layer translates between domain
models and ORM models. Table descriptions: [development practices](principles/development.md#database--migrations).

### `src/carcharoth/config/`
**Configuration & Validation**

- `app_config.py` — Pydantic schemas for the full config (strategy params, risk limits, watchlist)
- `settings.py` — `Settings` class that loads secrets from `.env`
- `config.yaml` — YAML file with watchlist, strategy name, params, risk limits (not gitignored,
  committed for reproducibility)

Config is validated at startup; invalid values fail fast with clear error messages.

### `src/carcharoth/` (root)
- `main.py` — composition root (see above)
- `__main__.py` — entry point (`python -m carcharoth`)
- `logging_setup.py` — rotating file handlers for app/errors/trades/decisions logs

---

## Data Flow

### One Tick (1 minute)

```
scheduler.tick()
   │
   ├─ order_executor.reconcile_fills()
   │  └─ OrderRepository.save() → DB
   │
   ├─ market_data.fetch_bars/quotes()
   │  └─ AlpacaMarketDataService (converts Alpaca → Bar/Quote)
   │
   ├─ account.fetch_state()
   │  └─ AlpacaAccountService (converts Alpaca → Account)
   │
   ├─ strategy.evaluate(market_data, account, positions)
   │  └─ returns Signal (BUY/SELL/HOLD)
   │
   ├─ risk_manager.approve_orders(signal, positions, account)
   │  └─ returns approved Order with size
   │
   └─ order_executor.submit_order(order)
      └─ AlpacaOrderExecutor (submits to Alpaca)
```

### Persistence

Every decision and fill is logged to the database, tagged with the `run_id` of the
producing run:
- `runs` — one row per app start / backtest (run_type, config, symbols, date range)
- `strategy_decisions` — signal, indicator values (JSONB), confidence
- `trades` — fills and execution records
- `orders` — order history
- `positions_snapshot` / `equity_snapshots` — per-symbol positions and total equity per tick
- `backtest_metrics` — analyzer output per backtest run (key/value, optional symbol)
- `configurations` — the config snapshot that was in effect

This enables post-trade analysis, regime/strategy evaluation and comparison between runs.

---

## Extension Points

### Add a Strategy
1. Create `strategies/my_strategy.py` → implement `Strategy`
2. Register in `strategies/registry.py`
3. Select in `config/config.yaml`

See also [design principles](principles/design.md#3-registry-pattern-for-extensibility).

### Add a Regime Feature (score detector)
1. Create `regime/features/my_feature.py` → implement `RegimeFeature`
2. Register in `regime/registry.py`
3. Add to `regime.score.features` in `config/config.yaml`

### Add a Regime Detector
1. Create `regime/my_detector.py` (or a package) → implement `RegimeDetector`
   from `interfaces/regime_detector.py`
2. Add a config section for it in `config/app_config.py` (hang it off `RegimeConfig`)
3. Add a builder entry in `regime/detectors.py` (and its emitted regimes to
   `EMITTED_REGIMES`)
4. Select it via `regime.detector` in `config/config.yaml`

### Swap the Broker
1. Create `services/newbroker/`
2. Implement `MarketDataService`, `AccountService`, `OrderExecutor`, `MarketClock`
3. Update `main.py` wiring

### Add Custom Risk Logic
1. Create `risk/my_risk_manager.py` → implement `RiskManager`
2. Update `main.py` to instantiate it

### Change What Gets Optimized
Edit `config/optimize.yaml` only — search-space entries are dot-paths into `config.yaml`.
New objective? Add it under `objectives:` in `config.yaml` and reference it by name.

### Swap the Optimization Library
1. Create `services/<library>/` → implement `ParameterOptimizer`
   (consume the `BacktestFunc` callable; read the run's `fitness_<objective>` metric)
2. Update `main.py` wiring — `optimize/` (overrides, constraints, bars cache) is reusable

---

## Testing Philosophy

Tests live in `tests/` with a 1-to-1 correspondence to `src/carcharoth/`. All tests use
**in-memory fakes** (`tests/fakes.py`) — no network, no database, deterministic and fast.
Integration tests (broker SDK, live DB) are spot-checked manually.

Details: [development practices — Testing](principles/development.md#testing).

---

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `alpaca-py` | Alpaca broker API |
| `sqlalchemy` | ORM |
| `alembic` | Database migrations |
| `pydantic` | Config validation |
| `optuna` | Parameter optimization (`carcharoth optimize`) |
| `redis` | Persistent bars/HMM-fit cache for backtests and optimize runs |
| `pandas` | Data analysis |
| `ta-lib` | Technical indicators (RSI, MACD, etc.) |
| `hmmlearn` | Gaussian HMM (regime detection, `regime/hmm/`) |
| `numpy`, `scipy` | Numerical computation |

For development: `pytest`, `mypy`, `ruff`.

---

## See Also

- [CLAUDE.md](CLAUDE.md) — developer guide index
- [operations.md](operations.md) — run the app, monitoring, troubleshooting
- [backtest.md](backtest.md) — backtest CLI and behaviour
- [optimize.md](optimize.md) — optimization CLI and behaviour
- [README.md](../README.md) — setup and getting started
- [config/config.yaml](../config/config.yaml) — example configuration
