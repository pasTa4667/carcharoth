# Quicktest Feature

## Overview

`quicktest` is a fast, isolated strategy tester that bypasses the full trading engine. It feeds historical bars directly into a strategy's `evaluate()` loop and simulates fills in-memory, with results persisted once at the end.

**Use case:** Rapidly test whether a strategy idea has any edge before running a full backtest.

## Architecture

- **Config-driven**: YAML file specifies symbols, date range, strategy name + params, capital, position sizing, and spread/slippage.
- **In-memory simulation**: Per-symbol independent loop with rolling lookback windows.
- **Fills**: Same-bar execution at close ± (spread/2 + slippage), matching `SimulatedBroker`.
- **Sizing**: Buy qty = `capital * position_size_pct / fill_price`; one position per symbol.
- **Results**: Dataclass holds trades, equity curves, round trips; all persisted as one batch at the end.
- **Metrics**: Reuses `analysis/metrics.py` for per-symbol and aggregate fitness scoring.

## Files

- `config/quicktest.yaml` — config template (symbols, dates, strategy params, optional `permutation:` section)
- `src/carcharoth/config/quicktest_config.py` — Pydantic validators + `load_quicktest_config()`
- `src/carcharoth/quicktest/` — simulator, result tracking, runner, and persistence
- `src/carcharoth/permutation/` — permutation-test runner, method registry, and methods
- `tests/test_quicktest.py` — unit tests for fill math, sizing, accounting, and end-to-end
- `tests/test_permutation.py` — permutation invariants, p-value math, batch persistence

## How to Run

```bash
# Edit config/quicktest.yaml with your symbols, dates, and strategy
uv run carcharoth quicktest --config config/quicktest.yaml

# Optional: add --verbose for debug output
uv run carcharoth quicktest --config config/quicktest.yaml --verbose
```

Output: a short console block (`run_id` + summary file link, like `backtest`). Full results are written to `logs/quicktest/{run_id}.yaml` — the quicktest config, aggregate metrics (sharpe, profit factor, drawdown, win rate, trade count), a `per_symbol` breakdown with the same metrics per symbol, and `fitness` scores.

## Permutation Testing

`--permute [METHOD]` wraps the quicktest in a permutation test: after the baseline run, the
bars are permuted and re-simulated `n_permutations` times (parallel worker processes, all
in-memory, one batched DB write at the end). The baseline's fitness is compared against the
permuted-score distribution; PASS when `p_value <= significance`.

```bash
# method from config/quicktest.yaml `permutation:` section
uv run carcharoth quicktest --permute

# override method and worker count from the CLI
uv run carcharoth quicktest --permute in_sample_bars --workers 4
```

- Methods implement the `PermutationMethod` interface (`interfaces/permutation.py`) and are
  registered in `src/carcharoth/permutation/registry.py`. Each method has a `kind`:
  - `in_sample_bars` (`kind="bars"`): shuffles gap + intrabar log-returns per symbol
    (warm-up bars stay real) and re-simulates — the p-value/PASS-FAIL flow above.
  - `monte_carlo_trades` (`kind="trades"`): the baseline runs **once**, then its closed
    round trips are resampled `n_permutations` times and the equity path rebuilt per sample
    (in-process, no re-simulation, no workers). `params.sampling: resample` (default,
    bootstrap with replacement) or `shuffle` (reorder — only path-dependent metrics vary).
    No verdict: the summary reports percentile tables (p5…p99) for total_return,
    max_drawdown, sharpe, profit_factor and final_equity, the observed run's percentile
    rank per metric, and the probability of profit.
- Reproducible: permutation `i` is seeded with `SeedSequence([seed, i])`, so results are
  identical for any worker count.
- Persistence: one `permutation_tests` header row (method, seed, observed score, p-value,
  verdict — the latter NULL for monte carlo) + one `permutation_results` row per permutation
  (score + headline metrics), cascade-deleted with the baseline run. Grafana: **Permutation
  Tests** dashboard.
- Summary YAML: `logs/permutation/{test_id}.yaml` (path printed on the console).

### Monte carlo for backtests

Because trade-based methods only need a finished run's trades, they also work on full
backtests: `uv run carcharoth backtest --permute [METHOD] --start ... --end ...` monte-carlos
the backtest's round trips after the run (settings from the optional `backtest.permutation:`
section in `config/config.yaml`; defaults to `monte_carlo_trades` when absent). Bar-based
methods are rejected there — they would need to re-run the engine per permutation.

## Deleting Runs

```bash
uv run carcharoth delete-run --run-id <uuid>          # delete one run + all its data
uv run carcharoth delete-run --all-quicktests         # delete every quicktest run
```

## What It Skips

- No regime detection or risk management
- No multi-strategy ensemble
- No position stacking (one open position per symbol)
- Session filtering only (pre-market and after-hours bars excluded)
