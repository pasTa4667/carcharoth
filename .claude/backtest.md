# Running a Backtest

Replays historical Alpaca data through the unchanged trading engine, as fast as possible
(no scheduler). All data is persisted under a new `run_id` with `run_type = BACKTEST`; the
analyzer runs automatically at the end and persists metrics to `backtest_metrics`.

For the architectural flow, see [Backtesting Flow](architecture.md#backtesting-flow) in
architecture.md.

```bash
uv run carcharoth backtest --start 2026-06-01 --end 2026-06-30            # --end inclusive
uv run carcharoth backtest --start 2026-06-01 --end 2026-06-30 --symbols AAPL,MSFT
uv run carcharoth analyze --run-id <uuid>            # recompute metrics for a run
uv run carcharoth delete-run --run-id <uuid>         # delete one run + all its data
uv run carcharoth delete-run --all-backtests         # delete every backtest run
uv run carcharoth cache stats                        # persistent-cache entry counts + memory
uv run carcharoth cache clear [--bars|--hmm]         # invalidate (both caches by default)
```

Simulation parameters (initial capital, spread, slippage) live in the `backtest` section of
`config/config.yaml`. The bar timeframe and warm-up window are derived from the configured
strategies (`required_bars()`), so there is no `--timeframe` flag. Results appear in the
**Backtest Results** Grafana dashboard (pick the run in the dashboard variable) — see
[operations.md](operations.md#monitoring).

Every backtest's analysis also scores the run against each named objective in
`config/config.yaml` (`objectives:` — weighted metric composites) and persists the score as
a `fitness_<name>` metric plus a `fitness:` block in the run's summary YAML
(`logs/backtests/<run_id>.yaml`) — so runs are comparable no matter what launched them.

High-volume append-only rows (decisions, equity/position snapshots, regime evaluations) are
write-buffered during a backtest and bulk-inserted in batches (`persistence/buffered.py`,
wired only in the backtest path) — live mode keeps one durable transaction per event.

Historical bars and fitted HMM models are cached persistently in Redis across runs (see
[Configuration](principles/development.md#configuration) in development practices): the first
backtest over a window fetches and fits everything, repeat runs gap-fill only what's missing.
Cached HMM fits are bit-identical to fresh ones (fitting is seed-deterministic), so results
don't depend on the cache; `--no-hmm-cache` skips the fit cache for one run.
