# Development Practices

## Testing
- All tests run against **in-memory fakes** (`tests/fakes.py`) — no network, no database
- Tests verify domain logic only; they don't test SDK integrations (those are spot-checked manually)
- Fixtures provide realistic objects (e.g., `FakePosition`, `FakeAccount`)

**Run tests:**
```bash
uv run pytest                  # all tests
uv run pytest -xvs tests/test_engine.py  # single file with output
```

## Configuration
- **Secrets** (`.env`, gitignored): API keys, database URL, Redis URL, Grafana credentials
- **Everything else** (`config/config.yaml`): watchlist, tick interval, bar timeframe, strategy
  parameters, risk limits, named fitness objectives (`objectives:` — weighted metric
  composites every backtest analysis scores itself against). Strategies are defined once under
  `strategies:` (keyed by name, each with its `params` and an `active` flag); `regime.active`
  toggles regime-driven mode. When `regime.active: false` the single `active` strategy trades
  everything; when `true` the detector picks per symbol via `regime.regimes` (which reference
  strategies by name — params still come from `strategies:`)
- **Regime detectors**: `regime.detector: score | hmm` selects the implementation; each has
  its own nested section (`regime.score:` — lookback, winsorize_sigma, features;
  `regime.hmm:` — training_window, refit_interval_bars, min_confidence, seed, feature
  periods, ...) including its own `evaluate_interval_minutes` (re-assessment cadence in
  market time; trading still happens every tick). The score detector emits
  `trending`/`mean_reverting`; the HMM emits `trending_up`/`trending_down`/`range_bound`/
  `high_volatility` with posterior probabilities — below `min_confidence` the previous
  regime is kept. Regimes without a `regime.regimes` mapping don't trade (open positions
  are managed hold-until-flat by the strategy that entered them)
- **Persistent cache** (`cache:` in config.yaml): backtest/optimize runs cache Alpaca bars
  and fitted HMM models in Redis (`REDIS_URL` in `.env`, compose service `redis`), so
  repeat runs over the same window start near-instantly. `enabled` is the master switch;
  `bars`/`hmm` toggle each cache. Disable the HMM cache (`cache.hmm: false` or
  `--no-hmm-cache`) when an Optuna study searches HMM params — every trial would get a
  fresh config hash and never hit. Live paper trading never uses the cache; unreachable
  Redis degrades to no caching with one warning. Manage with `carcharoth cache stats|clear`
- **Optimization studies** (`config/optimize.yaml`): search space (dot-paths into
  config.yaml), study budget, backtest window, constraints — see [optimize.md](../optimize.md)
- Config is validated with Pydantic at startup; invalid config fails fast

**Why:** Secrets are never committed; configuration is version-controlled and auditable.

## Database & Migrations
- Schema lives in `persistence/orm.py` (SQLAlchemy)
- Migrations are auto-generated via Alembic
- After changing `orm.py`:
  ```bash
  uv run alembic revision --autogenerate -m "describe change"
  uv run alembic upgrade head
  ```

**Tables:**
- `runs` — one row per app start or backtest (`run_type` PAPER | BACKTEST); all data tables
  below carry a `run_id` FK with cascade delete
- `trades` — order fills and execution records
- `orders` — order submissions and status history
- `positions_snapshot` — point-in-time portfolio state
- `equity_snapshots` — total equity/cash/buying power per tick (the equity curve)
- `strategy_decisions` — decision log with JSONB indicator values
- `configurations` — effective config of each run
- `regime_evaluations` — market regime classifications (JSONB feature evidence; the HMM
  detector also fills the nullable JSONB `probabilities` column with the full posterior)
- `strategy_assignments` — which regime → strategy assignment was active
- `backtest_metrics` — analyzer results per backtest run (key/value rows, optional symbol),
  including one `fitness_<objective>` row per named objective

**Optuna** (`carcharoth optimize`) keeps its own tables (`studies`, `trials`, ...) in a
dedicated `optuna` schema of the same Postgres (created automatically; Optuna's internal
`alembic_version` table would collide with carcharoth's in `public`). They are not part of
`orm.py`, and `alembic/env.py` additionally filters autogenerate to tables in
`Base.metadata`, so Optuna's tables are never picked up (or dropped) by migrations.

## Logging
- Rotating logs in `logs/`:
  - `app.log` — lifecycle, tick timing
  - `errors.log` — exceptions
  - `trades.log` — order submissions and fills
  - `decisions.log` — signals and risk verdicts with indicator values
- Log level defaults to INFO; can be adjusted in `config/config.yaml`

## Code Style
- Formatted with Ruff (`uv run ruff format`)
- Linted with Ruff (`uv run ruff check`)
- Max line length: 100 characters
- Imports organized: standard lib → third-party → local (enforced by Ruff)

## Documentation & Maintenance
- Keep [CLAUDE.md](../CLAUDE.md), the docs in `.claude/`, and [architecture.md](../architecture.md)
  in sync with the codebase
- **Major changes that require doc updates:**
  - Swapping/adding a broker or data provider → update `architecture.md` services section
  - Significant engine refactor or new orchestration step → update flow diagrams in `architecture.md`
  - Adding a new component layer or major interface change → update folder structure in both docs
  - New testing patterns or major tooling changes → update this file
  - Config schema changes → update the Configuration section above
- Minor fixes, bug fixes, and small feature additions within existing patterns don't need doc updates
- If you're unsure whether a change warrants a doc update, err on the side of updating — stale
  docs are worse than slightly verbose ones
