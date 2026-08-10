# Architecture Diagrams

Mermaid diagrams for the Carcharoth trading system.

Start with the [architecture overview](../architecture.md) for prose. Each topic
below is a Markdown page that explains the diagrams and links to the raw
`.mmd` source files, which live in [`diagrams/`](diagrams/). Keeping the source
as standalone `.mmd` files means you can render any single diagram on its own
without hunting through a large document.

## Pages

| Page | What it covers |
|------|----------------|
| [overview.md](overview.md) | System layers, dependency direction, composition root |
| [tick-sequence.md](tick-sequence.md) | The engine's fixed 5-step tick (live + backtest) |
| [engine.md](engine.md) | `TradingEngine` internals: per-symbol flow, order reconciliation, conflict clearing |
| [strategy-provider.md](strategy-provider.md) | Regime-driven strategy selection (hold-until-flat) |
| [regime-detection.md](regime-detection.md) | Score vs. HMM detectors and the assessment pipeline |
| [backtest.md](backtest.md) | Historical replay wiring and the simulated fill contract |
| [optimize.md](optimize.md) | Optuna study loop over unchanged backtests |
| [persistence.md](persistence.md) | Run-scoped tables and their relationships |

## Rendering a single diagram

All diagram sources are in [`diagrams/`](diagrams/) as `.mmd` files (one diagram
each, named `<page>-<topic>.mmd`). Render one with the Mermaid CLI:

```bash
# one-off, no install
npx -p @mermaid-js/mermaid-cli mmdc -i diagrams/engine-process-symbol.mmd -o engine-process-symbol.svg

# or if mermaid-cli is installed globally
mmdc -i diagrams/engine-process-symbol.mmd -o engine-process-symbol.svg
```

You can also paste a `.mmd` file's contents straight into the
[Mermaid Live Editor](https://mermaid.live) for a quick look. Many editors
(VS Code with the Mermaid extension, JetBrains IDEs) preview `.mmd` files
directly too.

Each `.md` page embeds a link + ready-to-run `mmdc` command next to every
diagram it references.

## Conventions

- **Solid arrows** = calls / depends on. **Thick arrows** = the main path.
- **Dotted arrows** = "implements", or an annotation pointing at a step.
- Interfaces (ABCs) are stadium/rounded nodes; concrete implementations are
  rectangles; datastores are cylinders; hexagons are side notes.

Colours are consistent across diagrams:

| Colour | Meaning |
|--------|---------|
| 🔵 blue | orchestration — engine, scheduler, the tick |
| 🟣 purple | interfaces/contracts, and pure support code |
| 🟡 amber | external I/O — Alpaca, the optimizer driving trials |
| 🟢 green | persistence — repositories, Postgres, run-scoped tables |
| 🟦 cyan | caches (Redis, in-process) |
| 🟠 teal | trading logic — strategies, regime detection, features |
| 🎀 pink | analysis — metrics, fitness |
| ⚪ grey | decisions / entry points |
| 🔴 red | abort / skip / failure paths |

Keep new diagrams to one idea each: if a diagram needs more than ~15 nodes, it
is probably two diagrams — or belongs as prose/a table instead.
</content>
