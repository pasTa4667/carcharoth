# Persistence & Run Scoping

Every live, backtest, or quicktest execution creates one row in `runs`. **All execution-data
repositories are run-scoped**: constructed with a `run_id`, they read and write only that
run's rows. This isolates backtests from live data and from each other, and
`carcharoth delete-run` removes a run with all its data via `ON DELETE CASCADE`.

`persistence/orm.py` defines the schema; `persistence/repositories.py` holds the
Repository ABCs plus their SQLAlchemy implementations.

> **Diagram:** [`persistence-run-scoping.mmd`](diagrams/persistence-run-scoping.mmd) — render with `mmdc -i diagrams/persistence-run-scoping.mmd -o persistence-run-scoping.svg`

## Write path

`TradingEngine` / `BacktestAnalyzer` → `SqlAlchemy*Repository` (run-scoped) → ORM
models (`orm.py`) → Postgres. On the backtest path only, `persistence/buffered.py`
bulk-inserts high-volume rows.

- **Domain ↔ ORM translation lives in the repositories.** The engine and
  analyzer only ever pass `domain/models.py` types.
- **`buffered.py` is wired only on the backtest path**, where per-tick row
  volume is high; live trading writes directly.

## Trade-offs of run scoping

- A restarted live process does **not** reconcile the previous run's open orders
  (negligible: DAY market orders fill in seconds).
- Model artifacts are filesystem objects referenced by run config/model decisions; they are not
  mutable database blobs. `model_diagnostics` contains only a bounded copy of each artifact's
  `report.json` for Grafana and is keyed by immutable `model_id`, not `run_id`.
- Model decisions and signed round trips remain auditable after the run completes.

Optuna study storage and migrations:
[development practices](../principles/development.md#database--migrations).
