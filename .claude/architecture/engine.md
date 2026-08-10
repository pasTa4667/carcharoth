# TradingEngine Internals

`engine/engine.py` is a deliberately "stupid" orchestrator: it sequences
services and passes typed domain objects between them. No trading logic lives
here. This diagram unpacks the two non-obvious internal behaviours —
**fill reconciliation** and **conflicting-order clearing** — that the
[tick sequence](tick-sequence.md) glosses over.

## tick() control flow

> **Diagram:** [`engine-tick-control-flow.mmd`](diagrams/engine-tick-control-flow.mmd) — render with `mmdc -i diagrams/engine-tick-control-flow.mmd -o engine-tick-control-flow.svg`

## _reconcile_fills()

Runs before anything else. It closes the loop on orders submitted in earlier
ticks.

> **Diagram:** [`engine-reconcile-fills.mmd`](diagrams/engine-reconcile-fills.mmd) — render with `mmdc -i diagrams/engine-reconcile-fills.mmd -o engine-reconcile-fills.svg`

The `exists_for_order` guard makes reconciliation **idempotent**: replaying a
tick (or a slow broker reporting FILLED twice) never double-records a trade.

## _process_symbol()

> **Diagram:** [`engine-process-symbol.mmd`](diagrams/engine-process-symbol.mmd) — render with `mmdc -i diagrams/engine-process-symbol.mmd -o engine-process-symbol.svg`

## _clear_conflicting_orders()

Guards against submitting an order while another is already in flight for the
same symbol — which would trip the broker's wash-trade protection or
double-execute.

> **Diagram:** [`engine-clear-conflicting-orders.mmd`](diagrams/engine-clear-conflicting-orders.mmd) — render with `mmdc -i diagrams/engine-clear-conflicting-orders.mmd -o engine-clear-conflicting-orders.svg`

**Why return `False` even after only same-side orders?** A same-side open order
means a duplicate is already working, so the new signal is redundant this tick.
An opposite-side order is cancelled now and the signal re-fires next tick once
the book is clear — Alpaca cancels asynchronously, so acting immediately is
unsafe.

## Constructor dependencies

`TradingEngine` takes ten collaborators, all of them interfaces or plain data —
which is why it is fully testable with in-memory fakes (`tests/fakes.py`):

| Dependency | Type |
|---|---|
| `market_data` | `MarketDataService` |
| `account` | `AccountService` |
| `strategies` | `StrategyProvider` |
| `risk` | `RiskManager` |
| `executor` | `OrderExecutor` |
| `decisions_repo` | `StrategyDecisionRepository` |
| `orders_repo` | `OrderRepository` |
| `trades_repo` | `TradeRepository` |
| `snapshots_repo` | `PositionSnapshotRepository` |
| `symbols` | `list[str]` |
</content>
