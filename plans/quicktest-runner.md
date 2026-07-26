# Isolated Strategy Quick-Test Runner (`carcharoth quicktest`)

## Context

Finding strategies with an edge currently requires a full backtest: TradingEngine tick
loop, regime detection, risk manager, and per-tick Postgres reads/writes (orders,
trades). That's slow and heavyweight for the "does this idea have any edge at all?"
question. We want a fast, isolated runner that:

- fetches historical bars, feeds them straight to a `Strategy.evaluate()` loop
- no engine, no regime detection, no risk manager
- keeps **everything in memory** (trades, equity curve, round trips, metrics)
- persists results **once, at the very end**
- is configured by a small dedicated YAML (symbols, time window, strategy + params)
- is built agnostic/extensible so trade- or market-data **permutation** can be added
  later (not in this iteration)

## Approach

New package `src/carcharoth/quicktest/` with a pure, in-memory simulation loop that
reuses the existing `Strategy` interface, bar fetching, and metric functions.

### Decisions (settled)

- **Sizing**: YAML gives `capital` (e.g. 10000) and `position_size_pct` (e.g. 0.10);
  BUY qty = `capital * position_size_pct / fill_price`. One open position per symbol
  (BUY ignored while long, SELL closes the whole position — matches engine behavior).
- **Portfolio model**: each symbol simulated **independently** with its own
  `capital` and equity curve. Aggregate equity curve = sum of per-symbol curves
  (valid because they are independent).
- **Fills**: same-bar fill at close ± spread/2 ± slippage, matching
  `SimulatedBroker`; `spread_pct` / `slippage_pct` in the YAML, defaulting to 0.
- **Persistence**: existing Postgres tables, single flush at the very end —
  `runs` (new `RunType.QUICKTEST`; `run_type` is a plain string column, **no
  migration needed**), `trades`, `round_trips`, `equity_snapshots` (aggregate
  curve), `backtest_metrics`. Enables a dedicated Grafana board.
- **Bars**: Redis `PersistentBarsCache` wrapping `fetch_historical_bars`, same
  wiring as `_run_backtest` (`_build_cache_stores` in main.py).

### Flow

1. **Config** — new `config/quicktest.yaml` + Pydantic loader
   (`config/quicktest_config.py`): symbols, start/end, strategy name + params,
   capital, position_size_pct, spread/slippage, objective name.
2. **Data** — `PersistentBarsCache(fetch_historical_bars)` + `warmup_window(spec)`
   from `strategy.required_bars()`, exactly like `run_backtest_once`.
3. **Simulation** — `QuickTestSimulator`: per symbol, walk regular-session bars
   (filter via `strategies/session.py`), maintain a rolling lookback window, call
   `strategy.evaluate(symbol, window, quote=None, position)`, fill signals with the
   SimulatedBroker fill model, track cash/position/equity in plain dataclasses.
   Everything accumulates in an in-memory `QuickTestResult` (trades as
   `TradeRecord`, per-symbol `EquityPoint` curves, `PositionSnapshot`s for MAE/MFE).
4. **Analysis (in-memory)** — reuse `analysis/metrics.py`: `match_round_trips` →
   `enrich_with_excursions` → `compute_metrics` per symbol (persisted as
   `MetricValue(..., symbol=sym)`) **and** on the aggregate (summed equity + all
   round trips). Fitness via `analysis/objective.py: score_metrics` using the named
   objective from `config.yaml objectives:` (quicktest.yaml just names it).
5. **Persist at the end** — one batch write: run row (create + finish), all
   `TradeRow`s via `WriteBuffer` (synthetic uuid `broker_order_id` per fill),
   equity via `EquitySnapshotRow` rows, `SqlAlchemyRoundTripRepository.save_all`,
   `SqlAlchemyBacktestMetricsRepository.save_metrics`. Position snapshots stay
   in-memory only (used for MAE/MFE, not persisted — minimal DB footprint).
6. **CLI** — new `quicktest` subcommand in `main.py` argparse
   (`carcharoth quicktest [--config config/quicktest.yaml] [--verbose]`), prints a
   console summary: sharpe, profit factor, fitness, max drawdown, win rate,
   num_trades — aggregate plus a compact per-symbol table.

### Extensibility hooks (for future permutation)

- The simulator takes `bars: dict[str, list[Bar]]` as plain input — a future
  permutation step is just a `dict -> dict` transform applied before the run.
- The in-memory result (trades/round-trips/equity) is a plain dataclass, so trade
  permutation = re-running metrics over a shuffled copy; the metrics functions are
  already pure.
- Define a tiny `BarsTransform`-style seam (identity for now) so permutation slots in
  without touching the loop. (Kept minimal — no speculative machinery.)

## Files to modify / create

| File | Change |
|---|---|
| `config/quicktest.yaml` | new — symbols, window, strategy, params, sim settings |
| `src/carcharoth/config/quicktest_config.py` | new — Pydantic models + `load_quicktest_config()` |
| `src/carcharoth/quicktest/simulator.py` | new — in-memory bar loop + fills + equity tracking |
| `src/carcharoth/quicktest/result.py` | new — in-memory result store dataclass |
| `src/carcharoth/quicktest/runner.py` | new — orchestration: fetch → simulate → analyze → persist |
| `src/carcharoth/domain/models.py` | add `RunType.QUICKTEST` (string column, no migration) |
| `src/carcharoth/main.py` | add `quicktest` subcommand + wiring |
| `tests/...` | unit tests for simulator + config loading |

### `config/quicktest.yaml` sketch

```yaml
symbols: [AAPL, MSFT, NVDA]
start: 2026-01-01        # YYYY-MM-DD (UTC)
end: 2026-03-31          # inclusive

strategy:
  name: mean_reversion
  params:
    entry_z: -1.5
    exit_z: 0.0

capital: 10000           # per symbol (independent portfolios)
position_size_pct: 0.10  # buy notional = capital * pct
spread_pct: 0.0          # 0 = frictionless; set >0 to match backtest
slippage_pct: 0.0
objective: default       # named objective from config.yaml `objectives:`
```

## Reuse (existing code, no changes needed)

- `interfaces/strategy.py: Strategy` + `strategies/registry.py: build_strategy()`
- `services/alpaca/historical.py: fetch_historical_bars, warmup_window`
- `services/cache/bars.py: PersistentBarsCache` (optional, config-gated)
- `analysis/metrics.py: match_round_trips, enrich_with_excursions, compute_metrics`
- `analysis/objective.py: score_metrics` + `objectives:` from base config
- `domain/models.py: Bar, Signal, Position, TradeRecord, EquityPoint, PositionSnapshot, MetricValue`
- `strategies/session.py: minutes_since_open/minutes_until_close` (regular-session filter)
- persistence repos in `persistence/repositories.py` for the final batch write

## Steps

- [x] Add `RunType.QUICKTEST` to `domain/models.py`
- [x] Add `config/quicktest.yaml` + `config/quicktest_config.py` (Pydantic models,
      `load_quicktest_config()`, validation: strategy name in registry,
      0 < position_size_pct ≤ 1, start < end)
- [x] Implement `quicktest/result.py` — `QuickTestResult` dataclass: trades,
      per-symbol equity curves, position snapshots; helper for aggregate equity
- [x] Implement `quicktest/simulator.py` — per-symbol independent loop, rolling
      lookback window, SimulatedBroker-style fills at close ± spread/2 ± slippage,
      sizing = capital × pct / price, one position per symbol, session filter
- [x] Implement `quicktest/runner.py` — build strategy via registry, fetch bars
      (Redis-cached) with warmup, run simulator per symbol, compute per-symbol +
      aggregate metrics and fitness in memory, then persist everything in one
      end-of-run batch (run row, trades via `WriteBuffer`, equity, round trips,
      metrics)
- [x] Wire `quicktest` subcommand into `main.py` (argparse + `_run_quicktest`
      composition, reusing `_build_cache_stores` / `build_data_client` /
      `build_engine` wiring)
- [x] Console summary output (aggregate + per-symbol table)
- [x] Unit tests: simulator fill math & round trips on synthetic bars; sizing;
      independent-per-symbol accounting; config loading/validation; end-of-run
      persistence using in-memory fakes (`tests/fakes.py`)

## Verification

- `uv run pytest` for new unit tests
- `uv run mypy` (strict) passes
- Manual: `carcharoth quicktest` with a short window (e.g. 1 month, 2 symbols,
  mean_reversion) → compare its metrics to a `carcharoth backtest` of the same
  window with regime/risk effectively disabled — numbers should be in the same
  ballpark; runtime should be dramatically lower
