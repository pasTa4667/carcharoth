# Running an Optimization

```bash
uv run carcharoth optimize \
  --set model.artifact_path=artifacts/models/<id> \
  --n-trials 20 --workers 4
```

Each Optuna trial runs a normal persisted out-of-sample backtest against the same immutable
artifact. Current search spaces tune decision hysteresis/thresholds and risk sizing. Paths that
change features, target/horizon, timeframe, benchmark, XGBoost parameters, training dates, or
artifact identity fail before trials because they require retraining.

Studies remain resumable in the dedicated Optuna schema. Trial user attributes link to run IDs;
constraints penalize the study value without modifying persisted fitness. In-process and Redis
bar caches are shared as before. See `architecture/optimize.md`.
