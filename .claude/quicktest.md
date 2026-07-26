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

- `config/quicktest.yaml` — config template (symbols, dates, strategy params)
- `src/carcharoth/config/quicktest_config.py` — Pydantic validators + `load_quicktest_config()`
- `src/carcharoth/quicktest/` — simulator, result tracking, runner, and persistence
- `tests/test_quicktest.py` — unit tests for fill math, sizing, accounting, and end-to-end

## How to Run

```bash
# Edit config/quicktest.yaml with your symbols, dates, and strategy
uv run carcharoth quicktest --config config/quicktest.yaml

# Optional: add --verbose for debug output
uv run carcharoth quicktest --config config/quicktest.yaml --verbose
```

Output: console summary (aggregate sharpe, profit factor, fitness, drawdown, win rate, trade count) + per-symbol table.

## What It Skips

- No regime detection or risk management
- No multi-strategy ensemble
- No position stacking (one open position per symbol)
- Session filtering only (pre-market and after-hours bars excluded)
