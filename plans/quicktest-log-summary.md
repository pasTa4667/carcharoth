# Quicktest: write results to a log file instead of console

## Context
Today `carcharoth quicktest` dumps its full result table to stdout via
`format_summary(...)` in `_run_quicktest` (`src/carcharoth/main.py:776`). Nothing
is persisted to a human-readable file, which makes later analysis awkward.

The `backtest` command already does the right thing: it writes a YAML summary to
`logs/backtests/{run_id}.yaml` via `write_backtest_summary(...)` and prints only a
short "backtest complete / run_id / summary path" block. We want quicktest to
behave the same way — write `logs/quicktest/{run_id}.yaml` and print just the
run_id + summary file link, dropping the verbose console table.

Add a `write_quicktest_summary(...)` helper in `logging_setup.py` that mirrors
`write_backtest_summary` but targets `logs/quicktest/` and captures the
quicktest-relevant data: aggregate metrics, fitness, a full **per-symbol metric
breakdown** (sharpe, profit_factor, total_return, max_drawdown, win_rate,
num_trades — the same columns the old console table showed), and the quicktest
config (strategy, params, sim settings, symbols, window). Then update
`_run_quicktest` to call it and print the short completion block instead of
`format_summary`, and delete `format_summary` (now dead code).

We reuse the existing metrics-flattening pattern (aggregate vs per-symbol vs
fitness) already implemented in `write_backtest_summary`.

## Files to modify
- `src/carcharoth/logging_setup.py` — new `write_quicktest_summary(...)`.
- `src/carcharoth/main.py` — import it, call it in `_run_quicktest`, replace the
  `print(format_summary(...))` with the short completion block.
- `.claude/quicktest.md` — update the "How to Run" / Output description.
- `src/carcharoth/quicktest/runner.py` — delete `format_summary`.
- `tests/test_quicktest.py` — remove any `format_summary` test; optionally add a
  test for the summary writer.

## Reuse
- `write_backtest_summary` (`src/carcharoth/logging_setup.py:81`) — structure and
  metric-flattening logic (`per_symbol` / `fitness` / flat split via
  `FITNESS_PREFIX`) to copy/adapt.
- The backtest console block (`src/carcharoth/main.py:400-403`):
  ```python
  print("backtest complete")
  print(f"  run_id:  {result.run_id}")
  print(f"  summary: {LOG_DIR / 'backtests' / f'{result.run_id}.yaml'}")
  ```
- `QuickTestOutcome` (`src/carcharoth/quicktest/runner.py:52`) — already carries
  `run_id` and `metrics`.
- `LOG_DIR = Path("logs")` (`src/carcharoth/main.py:107`).
- `QuickTestConfig.model_dump()` for the config block (strategy, params, symbols,
  window, capital, sizing, spread/slippage).

## Steps
- [ ] Add `write_quicktest_summary(log_dir, run_id, started_at, config, metrics)`
      in `logging_setup.py`, writing `logs/quicktest/{run_id}.yaml` with a
      `run_id` / `date` / `config` / `results` (+ `fitness`) shape like the
      backtest summary. Config block includes strategy name + params, symbols,
      start/end, capital, position sizing, spread/slippage. Under `results`,
      include the aggregate metrics plus a `per_symbol` map where each symbol
      holds the full metric breakdown (sharpe, profit_factor, total_return,
      max_drawdown, win_rate, num_trades).
- [ ] Import `write_quicktest_summary` in `main.py`.
- [ ] In `_run_quicktest`, capture `started_at`, call
      `write_quicktest_summary(...)` after the run, and replace
      `print(format_summary(...))` with:
      ```
      quicktest complete
        run_id:  {run_id}
        summary: logs/quicktest/{run_id}.yaml
      ```
- [ ] Delete `format_summary` from `quicktest/runner.py` and drop its import in
      `main.py` + any test referencing it.
- [ ] Update `.claude/quicktest.md` Output section.

## Decisions
- `format_summary` is deleted (dead code once the CLI stops using it).
- The quicktest YAML includes the full per-symbol metric breakdown, not just the
  backtest-style single-value-per-symbol map.

## Verification
- `uv run carcharoth quicktest --config config/quicktest.yaml` — confirm console
  now prints only the 3-line completion block and `logs/quicktest/{run_id}.yaml`
  exists with correct aggregate/per-symbol/fitness data.
- `uv run pytest tests/test_quicktest.py` — existing tests pass (adjust if
  `format_summary` is removed).
