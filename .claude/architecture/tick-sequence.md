# Tick Sequence

The single most important control flow in the system. The `Scheduler` calls
`TradingEngine.tick()` once per minute during market hours; the backtest runner
calls the **same** `tick()` once per historical bar. Only the injected
components differ.

> **Diagram:** [`tick-sequence.mmd`](diagrams/tick-sequence.mmd) — render with `mmdc -i diagrams/tick-sequence.mmd -o tick-sequence.svg`

## Notes

- **Step overview-layers runs first, deliberately.** Fills are reconciled at the *start* of
  the next tick, so a just-submitted order is recorded as `ACCEPTED` now and
  becomes a `Trade` on the following tick. The `SimulatedBroker` mimics this
  exactly: `submit()` returns `ACCEPTED` (filling internally), `get_order()`
  reports `FILLED`.
- **Directional decision.** Regime routing chooses a strategy family. While
  flat, that family arbitrates normalized long/short candidates; while open, it
  emits only the position direction's hold/close intent. The engine never
  compares raw strategy indicators.
- **Flatten first.** Opposite-direction entries are not evaluated while a
  position is open, so reversal requires a close fill and a later flat tick.
- **Per-symbol isolation.** An exception while processing one symbol is logged
  and swallowed so the remaining symbols in the tick still run.
- **Market-data failure aborts the whole tick** (there is nothing to trade on);
  everything else degrades per-symbol.

Detailed engine internals: [engine.md](engine.md).
</content>
