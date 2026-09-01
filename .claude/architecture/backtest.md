# Backtest Flow

`carcharoth backtest` runs the unchanged `TradingEngine` over historical regular-session bars.

- The artifact is validated before data replay, including the out-of-sample window.
- Historical bars include the tradable universe plus model context (SPY), but the timestamp grid
  is built only from tradable symbols.
- `HistoricalMarketDataService` exposes trailing completed bars at a movable cursor.
- `SimulatedBroker.submit()` queues ACCEPTED orders. At the next cursor,
  `mark_to_market()` fills from that bar's open with side-correct spread/slippage, then marks
  signed holdings at close. The engine reconciles the fill before producing the next intent.
- End-of-window pending orders are canceled, never filled from the signal bar's close.
- Long/short cash, equity, unrealized P&L, gross exposure, and margin buying power are signed.

Slim mode stores equity/trades/orders/round trips/metrics; `--verbose-db` additionally stores
model decisions and position snapshots. The same analyzer handles both directions.
