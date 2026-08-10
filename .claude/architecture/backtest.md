# Backtest Flow

`carcharoth backtest` replays historical data through the **unchanged**
`TradingEngine`. There is no scheduler: `BacktestRunner` drives one
`engine.tick()` per historical bar, as fast as possible. Only the wiring in
`main.py` differs from live trading.

> **Diagram:** [`backtest-flow.mmd`](diagrams/backtest-flow.mmd) — render with `mmdc -i diagrams/backtest-flow.mmd -o backtest-flow.svg`

## What gets swapped

| Live (paper) | Backtest |
|---|---|
| `AlpacaMarketDataService` | `HistoricalMarketDataService` — preloaded bars behind a movable cursor; quotes synthesized from the newest close ± half the configured spread |
| `AlpacaAccountService` + `AlpacaOrderExecutor` | `SimulatedBroker` — tracks cash/positions from an initial capital; market orders fill at close ± spread/slippage |
| `Scheduler` (minute ticks, market-hours gated) | `BacktestRunner` (one tick per session bar) |
| `AlpacaMarketClock` | not needed — the runner walks the bar grid |

Both replacements implement the same interfaces the engine already depends on,
so the engine cannot tell it is in a backtest.

## The fill contract

The subtlety that makes reuse of the live engine possible: `SimulatedBroker.submit()`
returns **ACCEPTED** (while filling internally), and `get_order()` then reports
**FILLED**. So the engine's normal step-0 reconcile records the trade on the
following tick — exactly as in live trading.

## Bars cache

Bar fetching is layered: an in-process union-window cache
(`optimize/bars_cache.py`, one per study) sits on top of `PersistentBarsCache`
(`services/cache/bars.py`, Redis, shared across runs and `--workers N`
processes), which sits on top of the one-shot Alpaca fetch.

Coverage never extends into the current UTC day, so partial intraday data is
never durably cached. Repeat runs over an already-covered window fetch nothing.

CLI, fitness scoring, and write buffering: see [../backtest.md](../backtest.md).
</content>
