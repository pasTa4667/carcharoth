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

Every run is persisted and analyzed into signed FIFO round trips, MAE/MFE, equity metrics, named
fitness scores, and `logs/backtests/<run_id>.yaml`. Default slim mode omits high-volume model
features/position rows; use `--verbose-db` when investigating decisions.

`--permute [METHOD]` performs trade-based Monte Carlo on the finished run. Bar methods remain a
quicktest operation.
