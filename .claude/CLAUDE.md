# Carcharoth — Developer Guide

Carcharoth is an interface-first, XGBoost-driven intraday trading/research platform. A scheduler
or replay runner drives the same deliberately simple engine. See [architecture.md](architecture.md)
for module ownership and flow diagrams.

## Invariants

- The engine orchestrates only; feature, prediction, decision, risk, and broker logic stay in
  their modules.
- Domain dataclasses are the only types crossing module interfaces. Alpaca/XGBoost SDK objects
  remain in their adapters.
- Features are pure, ordered, versioned, scale-free, and shared by training and inference.
- Artifacts are immutable and explicit. Runtime validates model hash, feature schema, task,
  target, timeframe, benchmark, and out-of-sample window before replay/live use.
- Completed bar `t` produces an intent whose replay fill is bar `t+1` open.
- Positions are signed; gross exposure uses absolute market value; reversals close first.
- Missing inference means `UNCHANGED`, not `FLAT`; end-of-day flatten overrides model failure.
- Regime detector source is retained but no runtime flow constructs or persists it.
- Strict mypy, Ruff, Pydantic `extra="forbid"`, deterministic tests, and in-memory fakes apply.

## Module map

```text
src/carcharoth/
  main.py                 composition root and CLI
  engine/                 fixed orchestration and scheduler
  features/               ordered feature registry/pipeline + session helpers
  modeling/               datasets, targets, artifacts, policy, signal generator, training flow
  services/xgboost/       only XGBoost-importing adapter
  services/alpaca/        Alpaca data/account/execution adapters
  services/backtest/      historical snapshots and signed next-open broker
  risk/                   target-transition checks and sizing
  quicktest/              fast fixed-artifact per-symbol simulation
  backtest/               full-engine replay and expanding-window loop orchestration
  analysis/               signed FIFO trips, MAE/MFE, metrics, objectives
  services/optuna/        fixed-artifact optimization adapter
  persistence/            run-scoped ORM/repositories/buffering
  config/                 strict layered config
  regime/                 disconnected detector source retained for future context
```

## Common changes

### Add a model feature

1. Implement one scalar `Feature` in `features/` with a stable output name, implementation
   version, warm-up requirement, and pure calculation.
2. Register it in `features/registry.py`.
3. Add its spec to the ordered `model.features` list and retrain.
4. Test values, warm-up, invalid data, schema fingerprint changes, and training/inference parity.

### Train and activate a model

```bash
uv run carcharoth train -p training
uv run carcharoth backtest --set model.artifact_path=artifacts/models/<id>
cargo run -p carcharoth-tui  # promote only after review
```

Never rewrite an artifact directory or auto-update paper config from training.
The TUI Backtest Loop tab / `backtest-loop` can repeat independent backtests with optional
fresh expanding-window training. Python owns the preview/schedule; each training return selects
that iteration's exact artifact. Nullable `runs.loop_context` groups UUID runs for Grafana without
changing config hashes. See [backtest.md](backtest.md#backtest-loops).

### Change model task

Replace atomic `model.target` with regression or classification config, train a new artifact,
and explicitly select it. The engine does not change; target builder, XGBoost objective/output,
and decision policy dispatch by the artifact task.

### Swap broker/data provider

Implement the relevant interface adapters, convert provider types at the adapter seam, and wire
once in `main.py`.

## Verification

```bash
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy src
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

Architectural/config/behavior changes must update README, these docs, relevant Mermaid sources,
and Grafana SQL. See [principles/design.md](principles/design.md) and
[principles/development.md](principles/development.md).
