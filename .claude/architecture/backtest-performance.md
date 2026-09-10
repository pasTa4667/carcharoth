# Backtest Performance

The CLI injects a `Monitor` into `run_backtest_once()`. Its execution scope owns a
context-local collector; stage helpers and decorators are inactive outside that scope.
This avoids threading telemetry parameters through trading interfaces. Context is reset
in `finally`; measurements are single-threaded, not a cross-thread aggregation facility.
Live trading, quicktests and Optuna callers remain unmonitored. The TUI's ordinary
backtest command benefits without Rust changes. Permutations after replay are excluded.

The interface hides nested clocks, bounded aggregation, checkpoint scheduling, slow-write
fallback, execution status and local reporting. `PerformanceSnapshot` and
`PerformanceStage` domain dataclasses cross the repository seam; the SQLAlchemy adapter
is the only publisher that knows PostgreSQL. Clocks and publisher are injectable for tests.

## Timing semantics

- Fixed stage names, at most 128 paths/eight nesting levels; fixed counters. No samples,
  symbol labels, SQL text/parameters or per-function traces are retained by the collector.
- Top-level stages: setup/model loading, fetch, simulation, final buffer flush, analysis,
  summary. Untimed orchestration remains explicit in the dashboard's coverage panel.
- Replay separates broker, market data, reconciliation, account/snapshot persistence,
  model, risk/execution, decision persistence and decision logging. Inside the model,
  features/prediction/policy are separate. Feature implementations stay pure.
- Repository timings include full order/trade calls, including transaction exit/commit;
  buffer-flush timings wrap actual SQL flushing, not merely buffer append operations.
  Trade/progress logging outside the explicit decision-log scopes is attributed to its
  enclosing stage or uncovered orchestration rather than mislabeled database time.
- Inclusive time includes children; exclusive time subtracts them. Rank exclusive times;
  never sum inclusive parents and children. Active-stage snapshots include elapsed work
  and count the current call (so live mean/max are provisional).
- CPU is current-process user+system CPU (including native threads), not child-process
  CPU. Overall and simulation CPU are coarse measurements, not per-stage CPU. CPU/wall
  is average cores; a gap is not proof of database waiting. Whole-execution CPU includes
  telemetry work, while stage wall clocks subtract publication time.
- `ticks` means grid timestamps across the watchlist, not symbol-bars. Symbol attempts,
  feature computations, predictions, repository calls and swallowed symbol errors are
  separate counters; skipped/duplicate/warming-up bars need not yield predictions.
- Whole-execution elapsed starts before model loading and ends after final publication;
  CLI startup/config/data-client construction and local final-report serialization are
  outside it. External command timing remains the end-to-end overhead baseline.

## Persistence and failures

`backtest_performance` owns an execution UUID independent of the eventual `run_id`.
`backtest_performance_stages` has one row per execution/path. Each publication atomically
upserts the cumulative header and stages. Updates overwrite, not append: Grafana shows
current cumulative state and history across runs, not an intra-run time series.

A dedicated pool (one connection, no overflow) sets pool/connect/statement/lock timeouts.
Normal checkpoint attempts occur at replay boundaries, at most once per configured
interval. No timers spawn a thread or write at stage entry/exit. A >100 ms or failed
publication disables further periodic attempts. Final publication still gets one attempt.
Connection establishment/network failures can exceed 100 ms; this is a measured fallback
budget, not a universal hard deadline. No retries occur every bar.

Initial publication precedes model loading. A failed artifact therefore has a performance
execution with no domain run. Return/exception/Ctrl-C produce `SUCCEEDED`/`FAILED`/
`INTERRUPTED`; hard kills may leave `RUNNING`. Staleness can also mean a slow fetch/tick,
so the dashboard does not infer failure. Final-only runs have no expected heartbeat.
Deleting a domain run sets the performance `run_id` null; deleting a performance header
cascades its stages. Execution metadata preserves config/artifact/workload correlation.

Publication failures log warnings and never replace workload exceptions or results.
The authoritative final local report is `logs/backtests/<execution-id>.performance.json`.
Database snapshots account for completed publications **before** the current transaction;
local final telemetry duration also includes the last transaction. Database totals therefore
lag the final local total by that last write; do not invent a zero-cost recursive last update.
Local report failures also only warn. No credentials are included in metadata.

See [the runbook](../backtest-performance.md) and
[flow diagram](diagrams/backtest-performance.mmd). This is a backtest-only implementation,
not the older all-research execution hierarchy described in the historical plan.
