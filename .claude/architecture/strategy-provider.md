# Strategy Provider

The `StrategyProvider` (`interfaces/strategy_provider.py`) decides *which*
strategy handles a symbol on a given tick. Two implementations live in
`engine/strategy_provider.py`.

Two implementations sit behind the ABC (`required_bars()` +
`resolve(symbol, bars, position, as_of)`):

- **`SingleStrategyProvider`** — every symbol trades the one configured strategy.
- **`RegimeStrategyProvider`** — regime-driven selection (the interesting one, below).

## RegimeStrategyProvider.resolve()

The core idea is **hold-until-flat**: a regime switch does not yank a strategy
out from under an open position. The entering strategy (which understands its
own exit logic) keeps managing the position until the symbol is flat; only then
does the new regime's strategy take over.

> **Diagram:** [`strategy-provider-resolve.mmd`](diagrams/strategy-provider-resolve.mmd) — render with `mmdc -i diagrams/strategy-provider-resolve.mmd -o strategy-provider-resolve.svg`

## _assess()

Detection is decoupled from action: every assessment is **persisted**, but a
low-confidence probabilistic assessment does not change the acted-on regime.

> **Diagram:** [`strategy-provider-assess.mmd`](diagrams/strategy-provider-assess.mmd) — render with `mmdc -i diagrams/strategy-provider-assess.mmd -o strategy-provider-assess.svg`

## State carried across ticks

`RegimeStrategyProvider` is the one engine-side component with meaningful
per-run state (rebuilt from the DB on construction):

| Field | Meaning |
|-------|---------|
| `_assignments` | current symbol → strategy assignment (the acted-on choice) |
| `_latest_regime` | most recent regime that cleared confidence (the pending target) |
| `_last_attempt` | last time the detector was *attempted* per symbol (throttles expensive detectors even on warm-up misses) |

On construction it calls `assignments_repo.load_current()` and drops any
persisted assignment pointing at a now-unmapped strategy.

Detector internals: [regime-detection.md](regime-detection.md).
</content>
