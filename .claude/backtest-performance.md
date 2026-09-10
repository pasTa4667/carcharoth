# Diagnosing a slow backtest

## Monitoring

Apply the additive migration with `uv run alembic upgrade head`, then open
**Backtest Performance** at `http://localhost:3333/d/backtest-performance`.
The dashboard uses the existing `carcharoth-pg` datasource; no new monitoring server,
Prometheus exporter, or profiler dependency is required.

```bash
# Always-on stage summary, local JSON, and ~10-second cumulative updates
uv run carcharoth backtest
# Explicit periodic update interval (the default)
uv run carcharoth backtest --performance-interval 10
# Explicit final-only mode (initial execution header is still persisted)
uv run carcharoth backtest --performance-interval 0
# Baseline with collection/publication disabled
uv run carcharoth backtest --no-performance
# Function-level replay profile: intentionally more expensive
uv run carcharoth backtest --profile-output logs/replay.pstats
uv run python -c "import pstats; pstats.Stats('logs/replay.pstats').strip_dirs().sort_stats('cumulative').print_stats(30)"
```

Add the same profile/artifact, `--start`, `--end`, and symbol overrides to every
comparison command. Windows must remain out of sample. `--no-performance` conflicts
with explicit interval/profile options. Intervals must be finite and non-negative.
Operational flags do not change the trading config hash; they are recorded separately.

The local report `logs/backtests/<execution-id>.performance.json` includes domain run ID,
config/artifact identity, workload counters, platform, CPU/wall totals, stage totals/counts/
maxima, and publication health. The app log prints the five largest exclusive stages.
Slim DB mode still writes decision logs and performs synchronous order/trade queries.

## Read the measurements

1. Select execution and check status, workload size, profile-output and database mode.
2. Compare fetch versus simulation versus analysis. A replay tick processes the watchlist;
   it is not one symbol-bar. Use simulation seconds as the overall-throughput denominator.
3. Rank **exclusive** stage times; inclusive model time includes feature and prediction
   children. Never add both. CPU/wall estimates average cores, not a database wait metric.
4. Check errors and coverage/unassigned time. Swallowed symbol exceptions still allow
   `SUCCEEDED`, exactly as before, but their count is visible.
5. If the database is slow/unavailable, periodic writes disable themselves. Read the final
   local JSON; absence of a fresh Grafana row is not evidence of a hung replay. Initial/final
   writes are still attempted. Staleness can reflect long fetches or ticks, not just kills.

## Profile only when needed

For lower-perturbation diagnosis, optionally use `py-spy record --pid <python-pid>
--duration 30 --rate 50 -o logs/replay.svg` while replay is underway. `py-spy` is not an
application dependency and is not automatically installed. macOS attachment may require
additional permissions; do not weaken system security to make it work. Native-stack
support depends on the tool/platform. macOS `sample <pid> 8 10 -file logs/native.txt`
is available without installing a profiler, but mostly exposes interpreter/native stacks,
not Python function names.

Use the built-in `--profile-output` fallback to inspect Python call counts and cumulative/
self time. It wraps replay only (not fetching or analysis), disables/dumps on exceptions,
and warns rather than masking an original error if output cannot be written. Deterministic
profiling can be much slower and cannot split CPU from I/O wait or resolve native internals.
Never use profiled throughput as the unprofiled performance baseline. Use distinct output
paths when retaining comparisons (an explicitly supplied existing path is overwritten).

## Overhead acceptance

Run `uv run python scripts/benchmark_backtest_performance.py -- --start YYYY-MM-DD --end YYYY-MM-DD`
(with an existing valid profile/artifact and out-of-sample dates). It retains command/log/timing
records under `logs/performance-benchmark/` and stops on any failed workload.
Interleave at least three matched runs of off/final-only/10-second modes with the same
artifact, window, symbols, database and cache state. Compare median whole-command wall
time and variance; target <=2% overhead for ordinary monitoring. Profiler runs are separate.
If periodic mode misses the target, use final-only; if stage collection misses it, reduce
instrumentation before making it the default. A fast write alone does not prove low total
observer overhead. The initial baseline and measured findings live in
`plans/backtest-performance-baseline.md` and `plans/backtest-performance-results.md`.

Migration reversibility/timeout/failure tests should use a disposable database, never a
production downgrade. The reporting tables retain history after domain-run deletion;
manage their retention separately if desired.
