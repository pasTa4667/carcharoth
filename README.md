# Carcharoth

Algorithmic stock trading bot running against the **Alpaca paper trading API**.

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
         1. Market Data      — bars + quotes for the watchlist  (Alpaca)
         2. Account Service  — equity, buying power, positions  (Alpaca, source of truth)
         3. Strategy Engine  — BUY / SELL / HOLD signal per symbol
         4. Risk Manager     — approve/reject + position sizing
         5. Order Executor   — submit approved orders           (Alpaca)
```

Every component is defined by an interface in `src/carcharoth/interfaces/` and wired together in
exactly one place: `src/carcharoth/main.py` (the composition root). Swapping a component never
requires changes anywhere else.

| Component | Interface | v1 implementation |
|---|---|---|
| Market data | `MarketDataService` | `services/alpaca/market_data.py` |
| Account/portfolio | `AccountService` | `services/alpaca/account.py` |
| Strategy | `Strategy` | `strategies/mean_reversion.py` |
| Risk | `RiskManager` | `risk/basic.py` |
| Execution | `OrderExecutor` | `services/alpaca/execution.py` |
| Market hours | `MarketClock` | `services/alpaca/clock.py` |
| Cache (optional) | `Cache` | `services/cache/noop.py` (no-op) |
| Persistence | repository ABCs | `persistence/repositories.py` (SQLAlchemy/Postgres) |

Alpaca is the **source of truth** for live account state; PostgreSQL stores our own history:
`trades`, `orders`, `positions_snapshot`, `strategy_decisions` (analysis log with indicators as
JSONB) and `configurations` (the effective config of each run).

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Docker.

```bash
# 1. Install dependencies (uv manages Python 3.12 automatically)
uv sync

# 2. Configure secrets — fill in your Alpaca paper API key + secret
cp .env.example .env

# 3. Start PostgreSQL
docker compose up -d

# 4. Create the schema
uv run alembic upgrade head

# 5. Run the bot
uv run python -m carcharoth
```

The bot ticks every minute while the market is open (checked via Alpaca's market clock), sleeps
while it is closed, and shuts down cleanly on Ctrl-C / SIGTERM.

## Configuration

- **Secrets** (`.env`, gitignored): Alpaca key/secret, database URL. See `.env.example`.
- **Everything else** (`config/config.yaml`): watchlist symbols, tick interval, bar timeframe,
  strategy name + parameters, risk limits. Validated with pydantic at startup.

## Logs

Written to `logs/` (rotating, 10 MB × 5):

| File | Content |
|---|---|
| `app.log` | application lifecycle, tick timing |
| `errors.log` | every error/exception |
| `trades.log` | order submissions and fills |
| `decisions.log` | every signal + risk verdict incl. indicator values |

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
3. Select it in `config/config.yaml` under `strategy.name` / `strategy.params`.

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
