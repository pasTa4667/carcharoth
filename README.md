![Carcharoth](assets/banner.png)

# Carcharoth

Algorithmic stock trading bot and research platform. Live trading runs against the **Alpaca
paper trading API**; backtests, quicktests, and Optuna optimization replay history through the
same engine (or a stripped-down simulator for quick iteration).

> **Disclaimer:** This bot is wired to a paper trading account. It is a learning/experimentation
> project, not financial advice. Do not point it at a live account without a serious review.

## Architecture

A scheduler triggers the **trading engine** every minute. The engine is a deliberately "stupid"
orchestrator: it calls the services in a fixed sequence and passes typed domain objects between
them — all trading logic lives inside the individual components.

```
Scheduler (1 min, market hours only)
   └─> TradingEngine.tick()
         0. Order Executor   — reconcile fills of open orders   -> trades table, trades.log
         1. Market Data      — bars + quotes for the watchlist  (Alpaca / sim replay)
         2. Account Service  — equity, buying power, positions  (Alpaca / sim broker)
         3. StrategyProvider — resolve Strategy per symbol      (single or regime-driven)
         4. Risk Manager     — approve/reject + position sizing
         5. Order Executor   — submit approved orders           (Alpaca / sim broker)
```

When `regime.active` is true, step 3 uses `RegimeStrategyProvider`: a per-symbol regime
detector picks which registered strategy trades each symbol. Regime is re-assessed on a slower
interval; open positions stay with the strategy that entered them until flat (hold-until-flat).

Every component is defined by an interface in `src/carcharoth/interfaces/` and wired together in
exactly one place: `src/carcharoth/main.py` (the composition root). Swapping a component never
requires changes anywhere else.

| Component | Interface | Implementation |
|---|---|---|
| Market data (live) | `MarketDataService` | `services/alpaca/market_data.py` |
| Market data (backtest) | `MarketDataService` | `services/backtest/market_data.py` |
| Account/portfolio (live) | `AccountService` | `services/alpaca/account.py` |
| Execution (live) | `OrderExecutor` | `services/alpaca/execution.py` |
| Sim broker (backtest) | `AccountService` + `OrderExecutor` | `services/backtest/broker.py` |
| Strategy routing | `StrategyProvider` | `engine/strategy_provider.py` |
| Strategy | `Strategy` | `strategies/mean_reversion.py`, `strategies/ema_vwap.py` |
| Regime detection | `RegimeDetector` | `regime/score_detector.py`, `regime/hmm/detector.py` |
| Risk | `RiskManager` | `risk/basic.py` |
| Market hours | `MarketClock` | `services/alpaca/clock.py` |
| Historical bars | `BarsFetcher` | `services/alpaca/historical.py` |
| Bars/HMM cache | `ByteStore` | `services/cache/redis_store.py`, `services/cache/bars.py` |
| Optimization | `ParameterOptimizer` | `services/optuna/optimizer.py` |
| Permutation testing | `PermutationMethod` | `permutation/methods/in_sample_bars.py`, `permutation/methods/monte_carlo_trades.py` |
| Persistence | repository ABCs | `persistence/repositories.py` (SQLAlchemy/Postgres) |

Alpaca is the **source of truth** for live account state. PostgreSQL stores run-scoped history:
every live session, backtest, or quicktest creates a row in `runs`; all other tables reference
it via `run_id` (cascade delete via `carcharoth delete-run`).

Core tables: `trades`, `orders`, `positions_snapshot`, `strategy_decisions` (signals +
indicators as JSONB), `equity_snapshots`, `round_trips` (FIFO closed positions with MAE/MFE),
`backtest_metrics`, `configurations`, `regime_evaluations`, `strategy_assignments`,
`permutation_tests` + `permutation_results` (permutation-test verdicts and score
distributions, cascade-deleted with their baseline run).

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Docker.

```bash
# 1. Install dependencies (uv manages Python 3.12 automatically)
uv sync

# 2. Configure secrets — fill in your Alpaca paper API key + secret
cp .env.example .env

# 3. Start PostgreSQL, Redis, and Grafana (and optionally the app container)
docker compose up -d

# 4. Create the schema
uv run alembic upgrade head

# 5. Run the bot
uv run carcharoth          # same as `uv run carcharoth run`
```

The bot ticks every minute while the market is open (checked via Alpaca's market clock), sleeps
while it is closed, and shuts down cleanly on Ctrl-C / SIGTERM.

## CLI

| Command | Purpose |
|---|---|
| `carcharoth` / `carcharoth run` | Live paper trading |
| `carcharoth backtest` | Replay history through the full engine (regime + risk) |
| `carcharoth backtest --permute [METHOD]` | Backtest + monte carlo trade analysis (how path-lucky was the run?) |
| `carcharoth quicktest` | Isolated strategy test — no engine, regime, or risk |
| `carcharoth quicktest --permute [METHOD] [--workers N]` | Quicktest + permutation test (is the edge real or luck?) |
| `carcharoth optimize` | Optuna parameter search over backtests |
| `carcharoth analyze --run-id <uuid>` | Recompute metrics for a run |
| `carcharoth delete-run --run-id \| --all-backtests \| --all-quicktests` | Delete a run and all data |
| `carcharoth cache stats` / `cache clear [--bars\|--hmm]` | Inspect or clear the Redis cache |

Also works: `uv run python -m carcharoth`.

## Configuration

Secrets (`.env`, gitignored): Alpaca key/secret, database URL, optional `REDIS_URL` and
`OPTUNA_DATABASE_URL`, Grafana credentials. See `.env.example`.

Everything else is one **layered config system**. Every run command resolves a *profile* into a
single validated `RunConfig` (pydantic, `extra="forbid"`):

```
config/
  base.yaml                 # THE structure: every key, with defaults (incl. all strategy params)
  symbols/                  # symbol universes (core51, megacap, smoke)
  strategies/               # strategy selection presets (mean_reversion, ema_vwap)
  optimization/             # Optuna search spaces (atomic: replaced wholesale)
  profiles/                 # quicktest, backtest, optimization, smoke
  trading/paper.yaml        # protected paper-trading profile
```

A profile declares its stack: `extends: [base, symbols/core51, strategies/mean_reversion]`,
then overrides values. Resolution order: `extends` chain (depth-first, in order) → profile body
→ CLI `--set path=value`. Merge rules: **dicts merge recursively, lists replace wholesale,
later layers win**. `base.yaml` owns the key structure — layers may only *override* existing
values (typos fail with the offending file and path); `null`/`{}` base values are open slots,
and `optimization.search_space` is replaced wholesale by whichever layer defines it.

Strategy params live **once** in the `strategies:` map; the quicktest and optimizer reference
them by name, so a quicktest and a backtest of the same strategy always test identical params.

Default profile per command (override with `-p`): `run` → `trading/paper`, `backtest` →
`backtest`, `quicktest` → `quicktest`, `optimize` → `optimization`.

```bash
uv run carcharoth quicktest -p smoke                       # swap the whole profile
uv run carcharoth backtest --set risk.max_open_positions=12
uv run carcharoth config list                              # profiles, symbol sets, presets
uv run carcharoth config resolve -p quicktest              # fully merged + validated config
uv run carcharoth config validate -p backtest [--json]     # exit 1 + errors when invalid
uv run carcharoth config diff -p smoke --against backtest  # leaf-level diff
uv run carcharoth config hash -p optimization              # content hash of the resolved config
uv run carcharoth config schema                            # JSON Schema (for tooling/TUIs)
```

Every run persists its exact resolved config (`runs.config`) and writes a `config_hash` plus a
`provenance` block (profile, layers, `--set` overrides) into its summary YAML — any result is
replayable: `carcharoth config hash -p <profile> [--set ...]` must reproduce the hash.

### Regime detection

Configured under `regime:` in `config/base.yaml`. Set `regime.active: true` to enable
regime-driven strategy routing; set it to `false` and mark one strategy `active: true` for
single-strategy mode.

Two detectors (`regime.detector: score | hmm`):

- **Score** — weighted evidence features (hurst, vol clustering, CUSUM, Wasserstein) on a
  trending ↔ mean-reverting axis. Emits `trending`, `mean_reverting`.
- **HMM** — per-symbol Gaussian HMM (hmmlearn) over log return, rolling log-volatility,
  EMA-distance, and ADX. Emits `trending_up`, `trending_down`, `range_bound`, `high_volatility`
  with posterior probabilities; uses `min_confidence` to avoid noisy switches.

Map regimes to strategies under `regime.regimes`. Unmapped regimes do not open new positions.
Implementation lives in `src/carcharoth/regime/`.

## Research workflow

Typical iteration loop:

```bash
# 1. Fast signal check — one strategy, no regime/risk, per-symbol capital
uv run carcharoth quicktest

# 2. Full engine replay with regime detection and risk manager
uv run carcharoth backtest --start 2025-01-01 --end 2025-06-30
# omit --start/--end to use the profile's data window

# 3. Parameter search (each trial is a persisted backtest run)
uv run carcharoth optimize --n-trials 20 --workers 2

# 4. Inspect results
uv run carcharoth analyze --run-id <uuid>
open http://localhost:3333   # Backtest Results dashboard
```

**Backtest** runs the same `TradingEngine` tick loop against simulated market data and a sim
broker. By default it skips high-volume tables (`strategy_decisions`, `positions_snapshot`,
`regime_evaluations`); pass `--verbose-db` to persist them. Summary YAML:
`logs/backtests/<run_id>.yaml`.

**Quicktest** bypasses the engine entirely: bars go straight into one strategy with fixed
position sizing and per-symbol independent portfolios, so cash contention does not mask
per-symbol edge. Summary YAML: `logs/quicktest/<run_id>.yaml`.

**Permutation test** (`carcharoth quicktest --permute [METHOD]`) checks whether the quicktest's
fitness beats luck: the baseline quicktest runs as usual, then its bars are permuted and
re-simulated `n_permutations` times in parallel worker processes (in-memory; one batched DB
write at the very end). The verdict is a right-tailed p-value of the observed score against the
permuted-score distribution — PASS when `p_value <= significance`. Methods live in
`src/carcharoth/permutation/methods/` behind the `PermutationMethod` interface and are
registered in `permutation/registry.py` (currently `in_sample_bars`: shuffles gap + intrabar
log-returns per symbol, preserving drift and OHLC shape while destroying serial structure).
Results land in `permutation_tests` / `permutation_results` (Permutation Tests dashboard) and
a summary YAML in `logs/permutation/<test_id>.yaml`.

**Monte carlo trade analysis** (`monte_carlo_trades`, `kind="trades"`) is different: the
strategy runs **once**, then its closed round trips are resampled `n_permutations` times and
the equity path rebuilt from each sample — no re-simulation, so it runs in-process in seconds
and works on top of **both** commands: `quicktest --permute monte_carlo_trades` and
`backtest --permute [METHOD]` (backtests support only trade-based methods; settings come from
the optional `backtest.permutation:` section of the resolved config). Two sampling modes via
`params.sampling`: `resample` (default; bootstrap with replacement — total return, drawdown
and profit factor all vary: does the edge rest on a few lucky trades?) and `shuffle` (reorder
without replacement — the same trades, so only path-dependent metrics vary: how lucky was the
trade ordering?). This is a path-risk analysis, not a significance test: instead of a PASS/FAIL
verdict it reports percentile tables (p5…p99) per metric, the observed run's percentile rank
within each distribution, and the probability of profit. Same tables/dashboard/summary paths;
the verdict columns are stored as NULL.

**Optimize** uses Optuna; study tables live in a dedicated `optuna` Postgres schema. Search
space keys are dot-paths into the resolved config (see `config/optimization/*.yaml`). Use
`--no-hmm-cache` when the study searches HMM parameters. Parallel workers re-resolve the same
profile + overrides and refuse to start if the config hash no longer matches the parent's.
Summary YAML: `logs/optimize/<study_name>.yaml`.

Redis caches historical Alpaca bars and fitted HMM models for backtest/optimize runs. Live
paper trading never uses the cache. Clear it after data or model changes:
`uv run carcharoth cache clear`.

## Logs

Rotating application logs in `logs/` (10 MB × 5):

| File | Content |
|---|---|
| `app.log` | application lifecycle, tick timing |
| `errors.log` | every error/exception |
| `trades.log` | order submissions and fills |
| `decisions.log` | every signal + risk verdict incl. indicator values |

Run summaries (backtest, quicktest, permutation, optimize) are written as YAML under
`logs/backtests/`, `logs/quicktest/`, `logs/permutation/`, and `logs/optimize/` respectively.

## Monitoring (Grafana)

Grafana runs as a Docker service and reads trading history from PostgreSQL via SQL panels.
The datasource and dashboards are provisioned from `grafana/provisioning/`.

```bash
# start the full stack (app, postgres, redis, grafana)
docker compose up -d

# if the postgres volume already existed before grafana was added, create the
# read-only DB user once (password must match GRAFANA_DB_PASSWORD in .env)
./scripts/sync-grafana-db-user.sh

# open grafana
open http://localhost:3333
```

Log in with `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` from `.env`. Provisioned dashboards
under **Dashboards → Carcharoth**:

| Dashboard | Use |
|---|---|
| Trading Overview | Live paper trading — positions, PnL, signals, trades, regime |
| Live Analysis | Live session analytics |
| Backtest Results | Backtest and optimize runs — equity, metrics, round trips |
| Permutation Tests | Permutation tests — p-value/verdict (bar methods) or sampled-metric distributions vs. observed (monte carlo) |

| `.env` variable | Purpose |
|---|---|
| `GRAFANA_ADMIN_USER` | Grafana UI login (default: `admin`) |
| `GRAFANA_ADMIN_PASSWORD` | Grafana UI password — pick any strong value |
| `GRAFANA_DB_PASSWORD` | Password for the `grafana_reader` Postgres role |

Grafana connects as `grafana_reader`, a read-only Postgres user. On a **fresh** database volume
the init script in `docker/postgres/init-grafana-reader.sql` creates that user automatically; on
an **existing** volume run `./scripts/sync-grafana-db-user.sh` so the role and password match
`.env`.

To verify Postgres has data:

```bash
docker compose exec db psql -U carcharoth -d carcharoth -c "\dt"
docker compose exec db psql -U carcharoth -d carcharoth -c \
  "SELECT 'strategy_decisions' AS t, COUNT(*) FROM strategy_decisions;"
```

If panels show no data but the database has rows, test the datasource under
**Connections → Data sources → Carcharoth Postgres → Save & test**, then re-run the sync script
and restart grafana: `docker compose restart grafana`.

Dashboard JSON lives in `grafana/provisioning/dashboards/`. Edit panels in the UI, then export
and commit if you want changes versioned.

## Development

```bash
uv run pytest              # unit tests (no network, no DB needed)
uv run ruff check          # lint
uv run ruff format         # format
uv run mypy src            # strict type check
```

Tests run entirely against in-memory fakes of the interfaces (`tests/fakes.py`) — this is the
payoff of the interface-first design.

### Adding a strategy

1. Implement the `Strategy` ABC (`interfaces/strategy.py`) in a new module under `strategies/`.
   `evaluate()` must stay pure — no I/O.
2. Register it in `strategies/registry.py` (one line).
3. Add it under `strategies:` in `config/base.yaml` with its params.
   - Single-strategy mode: set `active: true` on one strategy and `regime.active: false`.
   - Regime mode: map regimes to it under `regime.regimes` (the `active` flags are ignored).

### Adding a regime feature (score detector)

1. Implement `RegimeFeature` in `regime/features/`.
2. Register it in `regime/registry.py`.
3. Add it to `regime.score.features` in `config/base.yaml`.

### Swapping the broker / data provider

1. Create a new package under `services/<provider>/` implementing `MarketDataService`,
   `AccountService`, `OrderExecutor` and `MarketClock`. Keep SDK types inside the package;
   convert to domain models (`domain/models.py`) at the boundary, as `services/alpaca/mappers.py`
   does.
2. Change the wiring in `main.py`. Nothing else changes.

### Database migrations

Schema lives in `persistence/orm.py`. After changing it:

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```
