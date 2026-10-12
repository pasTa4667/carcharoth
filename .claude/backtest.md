# Running a Backtest

```bash
uv run carcharoth backtest \
  --set model.artifact_path=artifacts/models/<id> \
  --start 2025-01-01 --end 2025-06-30
```

The selected immutable artifact is validated before replay. The data window must begin after its
validation window. Replay uses the full engine/risk path, signed long/short positions, close-first
reversals, and next-bar-open fills with configured spread/slippage. Context SPY bars are fetched
without expanding the tradable grid.

`--set model.prepared_history=true` opts into one batch matrix per target after fetching,
before the replay profile. Only execution rows and the latest regular-session predecessor
are retained; exact missing/non-finite timestamps remain UNCHANGED. The model default is false
(profiles may override it; loops explicitly default to false):
EMA/ADX/ATR/RSI initialization and sparse slots can differ from legacy rolling snapshots.
Do not retrain, relax numerical tolerances, or enable by default without the P0 approval gate.

Every run is persisted and analyzed into signed FIFO round trips, MAE/MFE, equity metrics, named
fitness scores, and `logs/backtests/<run_id>.yaml`. Default slim mode omits high-volume model
features/position rows; use `--verbose-db` when investigating decisions.

`--permute [METHOD]` performs trade-based Monte Carlo on the finished run. Bar methods remain a
quicktest operation.

Backtests collect bounded stage timings and coarse CPU totals by default, with ~10-second
cumulative Grafana updates. `--performance-interval 10` sets the default explicitly;
`--performance-interval 0` selects final-only reporting and `--no-performance` disables it.
`--profile-output logs/replay.pstats` is opt-in, more expensive function-level profiling.
These flags leave trading config hashes unchanged. See the [performance runbook](backtest-performance.md).

## Backtest loops

`backtest-loop` and the **Backtest Loop** TUI tab share Python schedule validation. Select a
positive iteration count, a frozen universe artifact, and a 3/6/12-month backtest period.
Select one bucket with `--bucket`, or every published bucket with `--all-buckets`.
The TUI **all buckets** toggle is off by default. Turning it on hides the single-bucket selector.
All-bucket execution includes the combined `all` bucket and excludes diagnostic undersized clusters.
Each bucket runs the requested iterations sequentially with its own loop UUID and the shared label.
Every bucket is validated before any execution. Without training, the artifact must be compatible
with every selected bucket. A failure stops the invocation, including remaining buckets.
With `--train`, supply `--train-start`, `--train-period` and `--validation-period`.
Training keeps its original start and expands one backtest period each iteration; validation
and backtest move forward with unchanged durations. Without training, `--artifact` and
`--start` are required, and artifact manifest dates supply training chronology for validation.
The same profile (default `backtest`) configures both stages. Loop-owned dates, artifact,
universe and explicit `--prepared-history true|false` take precedence over shared overrides.

Calendar boundaries are computed from the original date, not from previously clamped dates.
For example, yearly training from 2020-01-01 plus quarterly validation/backtests yields:
iteration 1 train 2020, validate 2021-Q1, backtest 2021-Q2; iteration 2 train through
2021-Q1, validate 2021-Q2, backtest 2021-Q3. All ends are inclusive in config, and the final
backtest must end before today UTC. Models are freshly trained, not incrementally warm-started.

`--dry-run --json` previews and validates without credentials, network calls or database access.
`universe list --root artifacts/universes --json` lists validated snapshots and bucket sizes.
TUI F2 refreshes selectors. Enter previews the schedule with bucket names and iteration numbers.
Press Enter again to confirm Start. Edits invalidate the preview.
The Output tab reports bucket, loop ID, iteration, stage, dates, artifact, and resulting run ID.

Apply migration `e1f2a3b4c5d6` (`uv run alembic upgrade head`) before execution. The first failed
stage stops the loop; training failures cannot fall back to an old model. Each backtest uses
fresh capital, engine and positions. Output **x**/TUI quit kills the invocation's process tree,
not just the uv parent. An interrupted model's temporary directory or unfinished run can remain;
completed outputs are kept. There is no automatic cleanup, retry, resume or live activation.

**Backtest Loops** in Grafana selects by loop UUID (optional label is not a unique key).
It shows recorded/completed versus requested backtests, iteration windows/artifacts/universe,
portfolio metrics, independent equity returns and links to existing detail dashboards.
A run is complete only when analysis metrics exist, not just when replay set `finished_at`.
Training failures before a backtest starts remain in CLI output; no row is invented.
Independent returns must not be compounded or interpreted as one continuous portfolio.

Verification: `uv run pytest tests/test_backtest_loop.py tests/test_run_loop_context.py` and
`cargo test --workspace`. Opt into SQL tests with `CARCHAROTH_LOOP_TEST_URL` pointing at a
**migrated disposable** database, then run `tests/test_backtest_loop_postgres.py`. The ignored
Rust `cancellation_closes_real_uv_python_descendant_streams` test launches only a sleeping
Python child through uv; it never contacts trading services.
