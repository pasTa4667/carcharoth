# Carcharoth Architecture Overview

Carcharoth is a **composable, interface-first** trading system. Every component is swappable;
new implementations never require changes to existing code.

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
   ├─ fetch_historical_bars()          — one-shot Alpaca fetch incl. warm-up window
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
- **Optuna storage**: its own `optuna` schema in the same Postgres (`OPTUNA_DATABASE_URL`
  overrides; `services/optuna/storage.py` creates the schema and scopes the URL — Optuna's
  internal `alembic_version` would otherwise collide with carcharoth's). Studies are
  resumable by name. `alembic/env.py` also filters autogenerate to carcharoth tables.
- **Bars cache**: one in-process union-window cache (`optimize/bars_cache.py`) serves all
  trials; the warm-up prefix varies per trial, so the cache widens instead of re-fetching.

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
- `risk.py` → `RiskManager` — approve/reject orders and size positions
- `cache.py` → `Cache` — optional caching layer
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
  - `noop.py` → `NoOpCache` — no-op cache (current default)
- `optuna/` — Optuna adapter (the only package that imports optuna)
  - `optimizer.py` → `OptunaOptimizer` (implements `ParameterOptimizer`)
  - `search_space.py` — config-driven search space → `trial.suggest_*` mapping

All Alpaca SDK types stay inside `alpaca/`; external code works with domain models.

### `src/carcharoth/backtest/`
**Backtest Orchestration**

- `runner.py` — `BacktestRunner` — iterates the historical bar grid (regular session bars
  only, mirroring the live scheduler) and calls `engine.tick()` per bar; one tick == one bar,
  so tick-counted settings like `regime.evaluate_every_ticks` count bars in a backtest

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

Dynamically switches between strategies based on market conditions.

- `detector.py` — `RegimeDetector` — evaluates feature scores, classifies regime
- `models.py` — `Regime` enum (BULL, BEAR, etc.)
- `features/` — regime feature implementations (volatility, trend, etc.)
  - Each feature is a class that computes a signal (e.g., "volatility high?")
- `registry.py` — feature registry: `{"volatility": VolatilityFeature, ...}`

When enabled in config, `RegimeStrategyProvider` switches strategy based on detected regime
(e.g., "if BULL → run growth strategy, if BEAR → run defensive strategy").

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

All reads/writes to Postgres go through repositories; this layer translates between domain
models and ORM models.

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

### Add a Regime Feature
1. Create `regime/features/my_feature.py` → implement `RegimeFeature`
2. Register in `regime/registry.py`
3. Add to `config.regime.features` in `config/config.yaml`

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

Tests live in `tests/` with a 1-to-1 correspondence to `src/carcharoth/`:
- `test_engine.py` — engine orchestration
- `test_config.py` — config loading + validation
- `test_strategy.py` — strategy logic
- etc.

All tests use **in-memory fakes** (`tests/fakes.py`):
- `FakeMarketDataService` — returns fixed bars
- `FakeAccountService` — tracks account state
- `FakeOrderExecutor` — queues orders in memory
- etc.

This means:
- **No network calls** — tests run in milliseconds
- **No database** — pure in-memory state
- **Deterministic** — no flakiness
- **Fast feedback** — run all tests during development

Integration tests (broker SDK, live DB) are spot-checked manually; fakes cover logic.

---

## Deployment

### Local Development
```bash
docker compose up -d                    # Postgres + Grafana
uv run alembic upgrade head             # migrations
uv run python -m carcharoth             # run bot
```

### Docker
```bash
docker build -t carcharoth .
docker run --env-file .env carcharoth
```

The bot ticks every minute during market hours and shuts down cleanly on SIGTERM.

### Backtesting
```bash
uv run carcharoth backtest --start 2026-06-01 --end 2026-06-30 [--symbols AAPL,MSFT]
uv run carcharoth analyze --run-id <uuid>       # recompute metrics
uv run carcharoth delete-run --run-id <uuid>    # or --all-backtests
```

### Monitoring
- **Grafana Dashboards**: `http://localhost:3333`
  - *Trading Overview* — live positions, PnL, signals, trades (PAPER runs only)
  - *Live Analysis (Paper Trading)* — equity curve, drawdown, trades per paper run
  - *Backtest Results* — metrics, equity curve, per-symbol PnL per backtest run (empty
    until a backtest has been run; pick the run in the dashboard variable)
- **Logs**: `logs/` — app, errors, trades, decisions
- **Database**: query `trades`, `strategy_decisions`, etc. for analysis

---

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `alpaca-py` | Alpaca broker API |
| `sqlalchemy` | ORM |
| `alembic` | Database migrations |
| `pydantic` | Config validation |
| `optuna` | Parameter optimization (`carcharoth optimize`) |
| `pandas` | Data analysis |
| `ta-lib` | Technical indicators (RSI, MACD, etc.) |
| `numpy`, `scipy` | Numerical computation |

For development: `pytest`, `mypy`, `ruff`.

---

## See Also

- [CLAUDE.md](CLAUDE.md) — developer guidelines and common tasks
- [README.md](README.md) — setup, configuration, deployment
- [config/config.yaml](config/config.yaml) — example configuration
