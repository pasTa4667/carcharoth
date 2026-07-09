# Carcharoth — Developer Guide

**Carcharoth** is an algorithmic stock trading bot that runs against the Alpaca paper trading API.
It's a learning/experimentation project with a deliberately simple architecture: a scheduler
triggers a fixed-sequence trading engine every minute during market hours.

For a high-level overview of the codebase structure, see [architecture.md](architecture.md).

## Key Principles

### 1. Interface-First Design
- Every component is defined by an **interface** (ABC) in `src/carcharoth/interfaces/`
- Concrete implementations live in focused modules under `services/`, `strategies/`, `risk/`, etc.
- Wiring happens in **exactly one place**: `src/carcharoth/main.py` (the composition root)
- Swapping a component never requires changes anywhere else — just change the wiring

**Why:** This makes testing trivial (use fakes), refactoring safe, and swapping providers (broker,
data source, strategies) a localized change.

### 2. Type-First Development
- Strict mypy enabled (`tool.mypy.strict = true`)
- All function signatures are fully annotated
- Generic types and union types are preferred over `Any`
- Domain models are defined in `domain/models.py` and used consistently across layers

**Why:** Type checking catches bugs early and serves as machine-readable documentation.

### 3. Registry Pattern for Extensibility
- Strategies register themselves in `strategies/registry.py`
- Regime features register in `regime/registry.py`
- New implementations can be added without touching the composition root

**How to add a strategy:**
1. Create a new module under `strategies/`
2. Implement the `Strategy` ABC from `interfaces/strategy.py`
3. Add one line to `strategies/registry.py`: `_STRATEGIES["your_name"] = YourStrategy`
4. Select it in `config/config.yaml` under `strategy.name`

### 4. Layered Architecture
- **Interfaces** (`interfaces/`) — contracts
- **Domain models** (`domain/models.py`) — pure data, no I/O
- **Services** (`services/`) — provider implementations, type conversions at boundaries
- **Engine** (`engine/`) — orchestration logic
- **Persistence** (`persistence/`) — data access (SQLAlchemy)
- **Config** (`config/`) — Pydantic schemas, YAML loading

**Why:** Clear separation of concerns makes each layer testable and swappable.

### 5. Testing
- All tests run against **in-memory fakes** (`tests/fakes.py`) — no network, no database
- Tests verify domain logic only; they don't test SDK integrations (those are spot-checked manually)
- Fixtures provide realistic objects (e.g., `FakePosition`, `FakeAccount`)

**Run tests:**
```bash
uv run pytest                  # all tests
uv run pytest -xvs tests/test_engine.py  # single file with output
```

### 6. Configuration
- **Secrets** (`.env`, gitignored): API keys, database URL, Grafana credentials
- **Everything else** (`config/config.yaml`): watchlist, tick interval, bar timeframe, strategy
  parameters, risk limits
- Config is validated with Pydantic at startup; invalid config fails fast

**Why:** Secrets are never committed; configuration is version-controlled and auditable.

### 7. Database & Migrations
- Schema lives in `persistence/orm.py` (SQLAlchemy)
- Migrations are auto-generated via Alembic
- After changing `orm.py`:
  ```bash
  uv run alembic revision --autogenerate -m "describe change"
  uv run alembic upgrade head
  ```

**Tables:**
- `trades` — order fills and execution records
- `orders` — order submissions and status history
- `positions_snapshot` — point-in-time portfolio state
- `strategy_decisions` — decision log with JSONB indicator values
- `configurations` — effective config of each run
- `regime_evaluations` — market regime classifications
- `strategy_assignments` — which regime → strategy assignment was active

### 8. Logging
- Rotating logs in `logs/`:
  - `app.log` — lifecycle, tick timing
  - `errors.log` — exceptions
  - `trades.log` — order submissions and fills
  - `decisions.log` — signals and risk verdicts with indicator values
- Log level defaults to INFO; can be adjusted in `config/config.yaml`

### 9. Code Style
- Formatted with Ruff (`uv run ruff format`)
- Linted with Ruff (`uv run ruff check`)
- Max line length: 100 characters
- Imports organized: standard lib → third-party → local (enforced by Ruff)

### 10. Documentation & Maintenance
- Keep [CLAUDE.md](CLAUDE.md) and [architecture.md](architecture.md) in sync with the codebase
- **Major changes that require doc updates:**
  - Swapping/adding a broker or data provider → update `architecture.md` services section
  - Significant engine refactor or new orchestration step → update flow diagrams in `architecture.md`
  - Adding a new component layer or major interface change → update folder structure in both docs
  - New testing patterns or major tooling changes → update testing section in CLAUDE.md
  - Config schema changes → update configuration section in CLAUDE.md
- Minor fixes, bug fixes, and small feature additions within existing patterns don't need doc updates
- If you're unsure whether a change warrants a doc update, err on the side of updating — stale
  docs are worse than slightly verbose ones

## Common Tasks

### Adding a New Strategy
See **Registry Pattern** above, or refer to `strategies/mean_reversion.py` as an example.
Strategies must implement `Strategy.evaluate(market_data, account, positions) → Signal` and
stay pure (no I/O).

### Swapping the Broker (e.g., IB instead of Alpaca)
1. Create a new package `services/<provider>/` implementing the four key interfaces:
   - `MarketDataService` — bars + quotes
   - `AccountService` — equity, buying power, positions
   - `OrderExecutor` — submit orders
   - `MarketClock` — market hours
2. Convert provider-specific types to domain models (`domain/models.py`) at the boundary
3. Update the wiring in `main.py`

### Running the App
```bash
# Start dependencies (Postgres + Grafana)
docker compose up -d

# Run migrations
uv run alembic upgrade head

# Run the bot (ticks every minute during market hours)
uv run python -m carcharoth
```

The bot shuts down cleanly on Ctrl-C or SIGTERM.

### Monitoring
- **Grafana**: `http://localhost:3333` (see `.env` for credentials)
- **Logs**: `logs/` directory
- **Database**: `docker compose exec db psql -U carcharoth -d carcharoth -c "\dt"`

## Troubleshooting

**"No such table" error:** Run `uv run alembic upgrade head`

**Grafana shows no data:** Check Postgres connection under **Connections → Data sources**,
then restart: `docker compose restart grafana`

**Type errors:** Run `uv run mypy src` to see full type-checking report

**Lint/format issues:** Run `uv run ruff check --fix && uv run ruff format`

## File Structure Map

```
src/carcharoth/
├── main.py                # Composition root (wiring)
├── engine/                # Scheduler + trading logic
├── interfaces/            # Component contracts (ABCs)
├── services/              # Provider implementations
├── strategies/            # Trading strategies
├── domain/                # Pure domain models
├── risk/                  # Risk management
├── persistence/           # Data access (ORM, repos)
├── config/                # Configuration + validation
├── regime/                # Market regime detection
└── logging_setup.py       # Logging configuration

tests/
├── fakes.py               # In-memory test doubles
├── test_*.py              # Test modules (1-to-1 with src)
└── conftest.py            # Fixtures

config/
└── config.yaml            # App configuration (YAML)

alembic/
├── versions/              # Database migration scripts
└── env.py                 # Migration environment

grafana/
└── provisioning/          # Dashboard + datasource configs

docker/
├── postgres/              # DB init scripts
└── compose file
```

## Links
- [Alpaca API Docs](https://docs.alpaca.markets)
- [Alembic Migration Guide](https://alembic.sqlalchemy.org)
- [Pydantic Documentation](https://docs.pydantic.dev)
- [SQLAlchemy ORM](https://docs.sqlalchemy.org/en/20)
