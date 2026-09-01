# TradingEngine Internals

`TradingEngine` is a deliberately simple orchestrator.

1. Reconcile open broker orders; terminal fills are recorded idempotently.
2. Ask `ModelSignalGenerator.market_requirements()` for bars/context.
3. Fetch completed bars and quotes for tradable + context symbols.
4. Fetch account state and persist signed positions/equity.
5. For each tradable symbol only, generate target exposure, persist it, assess risk, clear
   conflicting orders, and submit an approved side/quantity.

Per-symbol exceptions are logged and do not stop other symbols. A market-data failure aborts the
tick. `UNCHANGED` is persisted but never reaches risk. `FLAT` is actionable when a position is
open. Risk returns the actual order side, which is necessary because BUY can enter long or close
short, while SELL can close long or enter short.

The engine retains its open-order conflict and fill reconciliation behavior. Reversals are
close-first because risk returns only the closing quantity while an opposite signed position is
open.

Mermaid sources: `diagrams/engine-tick-control-flow.mmd`,
`diagrams/engine-process-symbol.mmd`, and reconciliation/conflict diagrams.
