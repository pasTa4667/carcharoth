# System Overview

Carcharoth is interface-first: every component is defined by an ABC in
`interfaces/`, implemented by concrete providers in `services/` (and friends),
and wired together in exactly one place — `main.py` (the composition root).
Dependencies point **inward**, toward the interfaces and pure domain models.

## Layered dependency view

> **Diagram:** [`overview-layers.mmd`](diagrams/overview-layers.mmd) — render with `mmdc -i diagrams/overview-layers.mmd -o overview-layers.svg`

## Why this shape

- **The engine never imports a provider.** It only touches `interfaces/` and
  `domain/`, so swapping Alpaca for another broker changes `main.py` plus one
  new `services/<provider>/` package — nothing in `engine/`.
- **`main.py` is the only wiring seam.** It decides live vs. backtest, which
  strategy/detector/risk policy runs, and injects everything.
- **Domain models are the lingua franca.** Provider SDK types are converted to
  `domain/models.py` at the boundary (e.g. `services/alpaca/mappers.py`) and
  never leak inward.

See [overview prose](../architecture.md#folder-structure--responsibilities) for
the per-folder responsibilities.
</content>
