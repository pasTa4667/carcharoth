# Carcharoth — Developer Guide

**Carcharoth** is an algorithmic stock trading bot that runs against the Alpaca paper trading API.
It's a learning/experimentation project with a deliberately simple architecture: a scheduler
triggers a fixed-sequence trading engine every minute during market hours.

For a high-level overview of the codebase structure, see [architecture.md](architecture.md).

## Key Principles

- [Design principles](principles/design.md) — interface-first design, types, registries, layers
- [Development practices](principles/development.md) — testing, config, database, logging, style

## Common Tasks

### Adding a New Strategy
See [Registry Pattern](principles/design.md#3-registry-pattern-for-extensibility), or refer to
`strategies/mean_reversion.py` as an example. Strategies must implement
`Strategy.evaluate(market_data, account, positions) → Signal` and stay pure (no I/O).

### Swapping the Broker (e.g., IB instead of Alpaca)
1. Create a new package `services/<provider>/` implementing the four key interfaces:
   - `MarketDataService` — bars + quotes
   - `AccountService` — equity, buying power, positions
   - `OrderExecutor` — submit orders
   - `MarketClock` — market hours
2. Convert provider-specific types to domain models (`domain/models.py`) at the boundary
3. Update the wiring in `main.py`

More extension points (regime features, risk managers, optimization library) are in
[architecture.md](architecture.md#extension-points).

## Workflows

- [Running the app, monitoring & troubleshooting](operations.md)
- [Running a backtest](backtest.md)
- [Running an optimization](optimize.md)
- [Running a quicktest](quicktest.md)

## File Structure Map

```
src/carcharoth/
├── main.py                # Composition root (wiring) + CLI subcommands
├── engine/                # Scheduler + trading logic
├── interfaces/            # Component contracts (ABCs)
├── services/              # Provider implementations (alpaca/, backtest/, cache/, optuna/)
├── strategies/            # Trading strategies
├── domain/                # Pure domain models
├── risk/                  # Risk management
├── backtest/              # Backtest runner (historical replay loop)
├── analysis/              # Post-run metrics + analyzer + objective (fitness) scoring
├── optimize/              # Optimization support logic (overrides, constraints, bars cache)
├── persistence/           # Data access (ORM, repos)
├── config/                # Configuration + validation
├── regime/                # Market regime detection
└── logging_setup.py       # Logging configuration

tests/
├── fakes.py               # In-memory test doubles
├── test_*.py              # Test modules (1-to-1 with src)
└── conftest.py            # Fixtures

config/
├── config.yaml            # App configuration (YAML)
└── optimize.yaml          # Optimization study configuration (search space, budget)

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
- [Optuna Documentation](https://optuna.readthedocs.io)
- [Alembic Migration Guide](https://alembic.sqlalchemy.org)
- [Pydantic Documentation](https://docs.pydantic.dev)
- [SQLAlchemy ORM](https://docs.sqlalchemy.org/en/20)
