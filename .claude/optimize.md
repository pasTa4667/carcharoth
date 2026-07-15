# Running an Optimization

`carcharoth optimize` runs an Optuna study: each trial suggests parameter values from the
search space in `config/optimize.yaml` (dot-paths into config.yaml, e.g.
`strategies.mean_reversion.params.entry_z`), runs a **normal fully-persisted backtest**
with the overridden config, and maximizes the run's `fitness_<objective>` metric. Which
parameters are optimized is pure configuration — no code changes.

For the architectural flow, see [Optimization Flow](architecture.md#optimization-flow) in
architecture.md.

```bash
uv run carcharoth optimize                                   # config/optimize.yaml
uv run carcharoth optimize --n-trials 20 --study-name probe  # budget/identity overrides
uv run carcharoth optimize --n-trials 20 --workers 4         # 4 parallel worker processes
```

- Studies are **resumable**: re-running with the same study name continues it (Optuna's
  storage defaults to `DATABASE_URL`; override with `OPTUNA_DATABASE_URL` in `.env`)
- `--workers N` (or `study.workers` in optimize.yaml) splits the trial budget across N
  worker processes coordinating through the shared Optuna storage — near-linear speedup.
  Each worker logs to its own files (`app.w<i>.log`, …); workers share bars and HMM fits
  through the persistent Redis cache, so only the first one fetches/fits cold. With
  `workers > 1` a configured `sampler_seed` is derived per worker (seed+index), so seeded
  studies are **not reproducible** run-to-run
- Trial runs are indistinguishable from manual backtests; the trial → `run_id` linkage
  lives only in Optuna's trial user attributes
- Hard `constraints` in optimize.yaml (e.g. `num_trades` ≥ 20) steer the sampler via a
  penalty score; the run's stored fitness stays the honest weighted score
- A study summary is written to `logs/optimize/<study_name>.yaml`; browse trials with
  `uvx --with 'psycopg[binary]' optuna-dashboard '<storage url>'` — the exact URL (scoped
  to the `optuna` schema) is printed at the end of every optimize run
- Historical bars are cached in-process across trials (one fetch per distinct timeframe)
  on top of the persistent Redis cache; fitted HMM models are reused across trials, runs
  and workers whenever the HMM config is unchanged. Use `--no-hmm-cache` when the study
  searches `regime.hmm.*` params
