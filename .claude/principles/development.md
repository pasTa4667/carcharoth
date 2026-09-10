# Development Practices

## Testing and quality

Tests use in-memory interface adapters and deterministic synthetic bars/models. Feature tests
cover values/order/schema/warm-up/context; dataset tests cover target timing and leakage; broker,
risk, and metrics tests cover signed positions and next-open fills.

```bash
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy src
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

## Configuration and artifacts

Secrets remain in `.env`. Strict versioned config lives under `config/`. `artifacts/` and `logs/`
are runtime outputs and gitignored. Never mutate an artifact directory; train a new one and select
it explicitly. On macOS install `libomp` for native XGBoost.

## Database

Schema lives in `persistence/orm.py`; every event is run-scoped. Core model-era tables are
`model_decisions`, `orders`, `trades`, `positions_snapshot`, `equity_snapshots`, `round_trips`,
`backtest_metrics`, and permutation tables. Regime evaluation history may remain from older runs,
but current runtime never writes it. `backtest_performance` and
`backtest_performance_stages` store cumulative execution telemetry (nullable run linkage,
retained after run deletion). Test migration reversibility on a disposable database.
Performance tests use injected clocks and publishers; real overhead comparisons interleave
matched workloads and report variance rather than enforcing flaky wall-clock unit assertions.

After ORM changes:

```bash
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

## Logging

`app.log`, `errors.log`, `trades.log`, and `decisions.log` remain rotating logs. Decision logs
include artifact ID, target exposure, raw prediction, configured feature vector, and risk result.

## Documentation

Architectural, config, behavior, CLI, persistence, and dashboard changes must update README,
`.claude/`, Mermaid sources, and Grafana SQL in the same change.
