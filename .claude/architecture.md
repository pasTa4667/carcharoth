# Carcharoth Architecture Overview

Carcharoth is a **composable, interface-first** trading system. Every component is swappable;
new implementations never require changes to existing code.

For design principles and day-to-day workflows, see [CLAUDE.md](CLAUDE.md) and its linked docs.
For visual companions to this document, see the [architecture diagrams](architecture/) (Mermaid).

## Diagrams

Mermaid diagrams live in [`architecture/`](architecture/) and visualise the
structures described below:

| Diagram | Companion to |
|---------|--------------|
| [overview](architecture/overview.md) | Folder structure & responsibilities (this doc) |
| [tick-sequence](architecture/tick-sequence.md) | Core Flows — live tick |
| [engine](architecture/engine.md) | `src/carcharoth/engine/` internals |
| [strategy-provider](architecture/strategy-provider.md) | Regime-driven strategy selection |
| [regime-detection](architecture/regime-detection.md) | `src/carcharoth/regime/` |
| [backtest](architecture/backtest.md) | Core Flows — backtest |
| [optimize](architecture/optimize.md) | Core Flows — optimize |
| [persistence](architecture/persistence.md) | Core Flows — run tracking |

## Core Flows

The control flows are documented as Mermaid diagrams (linked below). In brief:

- **Live tick** — the `Scheduler` calls `TradingEngine.tick()` every minute during market
  hours. Each tick runs a fixed 5-step sequence (reconcile fills → market data → account
  state → strategy → risk → execute), passing **typed domain objects** between components.
  The engine is "stupid" — it only orchestrates; all logic lives in the components.
  → [tick-sequence](architecture/tick-sequence.md), [engine internals](architecture/engine.md)
- **Backtest** — the *same* `TradingEngine` replays historical bars via `BacktestRunner`
  (no scheduler; one tick per bar). Only the wiring differs: `HistoricalMarketDataService`
  and `SimulatedBroker` replace the Alpaca services in `main.py`.
  → [backtest](architecture/backtest.md), CLI + fitness detail in [backtest.md](backtest.md)
- **Optimize** — `carcharoth optimize` wraps the unchanged backtest flow in an Optuna study;
  each trial is a normal, fully persisted backtest run. `services/optuna/` is the only
  optuna-importing package.
  → [optimize](architecture/optimize.md), CLI detail in [optimize.md](optimize.md)
- **Run tracking** — every start creates a row in `runs` (PAPER or BACKTEST) and all data
  repositories are **run-scoped**, isolating runs from each other.
  → [persistence](architecture/persistence.md)

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
interfaces in order. Diagrams: [engine internals](architecture/engine.md),
[strategy provider](architecture/strategy-provider.md).

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
  (`HistoricalMarketDataService` + `SimulatedBroker`) — see
  [backtest diagram](architecture/backtest.md) for the wiring swap and fill contract
- `cache/` — caching implementations (`NoOpCache`, `RedisByteStore`/`build_redis_store()`,
  `ResilientByteStore`, and `PersistentBarsCache` — the cross-run Redis bars cache;
  `redis_store.py` is the only module that imports `redis`) — see
  [backtest diagram](architecture/backtest.md#bars-cache)
- `optuna/` — Optuna adapter, the only package that imports optuna
  (`OptunaOptimizer` + config-driven `search_space.py`)

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

- `score_detector.py` — `ScoreRegimeDetector` — evidence-based feature scoring on a
  trend ↔ mean-reversion axis. Emits `trending` / `mean_reverting`.
- `hmm/` — `HmmRegimeDetector` — a per-symbol Gaussian HMM (hmmlearn), lazily fitted and
  periodically refit, with an optional persistent `HmmFitCache`. Emits `trending_up` /
  `trending_down` / `range_bound` / `high_volatility`.
- `models.py` — `Regime` enum, `Evidence`, `RegimeAssessment`, `StrategyAssignment`
- `features/` + `registry.py` — score-detector feature implementations and their registry
- `detectors.py` — `build_detector(RegimeConfig)` — config → concrete detector

`RegimeStrategyProvider` (in `engine/`) re-assesses each symbol's regime periodically,
persists the evaluation, and switches strategy hold-until-flat. Full detail:
[regime-detection](architecture/regime-detection.md) and
[strategy-provider](architecture/strategy-provider.md) diagrams; config reference in
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

- [architecture/](architecture/) — Mermaid diagrams for the flows and components above
- [CLAUDE.md](CLAUDE.md) — developer guide index
- [operations.md](operations.md) — run the app, monitoring, troubleshooting
- [backtest.md](backtest.md) — backtest CLI and behaviour
- [optimize.md](optimize.md) — optimization CLI and behaviour
- [README.md](../README.md) — setup and getting started
- [config/config.yaml](../config/config.yaml) — example configuration
