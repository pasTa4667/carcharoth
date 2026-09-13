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
are retained; exact missing/non-finite timestamps remain UNCHANGED. The default stays false:
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
