# Tick Sequence

The scheduler calls `TradingEngine.tick()` during market hours; replay calls the same method once
per regular-session bar.

```text
reconcile fills
-> completed bars/quotes for tradables + SPY
-> account/signed positions snapshot
-> ModelSignalGenerator per tradable symbol
-> persist model intent
-> RiskManager transition/size
-> clear conflicting open orders
-> submit
```

Fills are reconciled at the start of the next tick. In replay, an accepted order is filled from
the next bar open before reconciliation. Context symbols do not receive model/risk/order calls.
Per-symbol errors are isolated; a total market-data failure aborts the tick.

See `diagrams/tick-sequence.mmd` and `engine.md`.
