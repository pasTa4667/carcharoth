# Optimization Flow

`carcharoth optimize` wraps the **unchanged** backtest flow in an Optuna study.
`services/optuna/` is the only package that imports optuna; the backtest side
knows nothing about the optimizer.

> **Diagram:** [`optimize-flow.mmd`](diagrams/optimize-flow.mmd) — render with `mmdc -i diagrams/optimize-flow.mmd -o optimize-flow.svg`

## Key invariants

- **Runs stay independent.** A trial's run is a normal, fully persisted
  backtest; the backtest layer has no idea it is inside a study. The linkage
  (`trial.user_attr run_id`) lives on the Optuna side only.
- **Fitness is an analysis output, not an optimizer computation.** Every
  backtest scores itself against each named objective (`objectives:` in
  `config.yaml`) and persists `fitness_<name>`. The optimizer just reads it.
- **Config-driven search space.** `config/optimize.yaml` maps dot-paths to
  distributions — changing what gets optimized needs no code change. Strict
  paths mean a typo'd entry fails fast instead of silently optimizing a dead
  parameter.
- **Invalid combinations fail the trial, not the study.** An override that
  doesn't survive `AppConfig` validation marks that trial failed and the search
  continues.
- **Constraint violations only steer the search.** A violated hard constraint
  returns a penalty score as the objective value; the run's *persisted* fitness
  is untouched.

## Layering

| Package | Role |
|---|---|
| `optimize/overrides.py` | dot-path overrides on the raw config dict (strict) |
| `optimize/constraints.py` | hard-constraint checks on trial metrics |
| `optimize/bars_cache.py` | in-process union-window bar cache across trials |
| `services/optuna/optimizer.py` | `OptunaOptimizer` (implements `ParameterOptimizer`) |
| `services/optuna/search_space.py` | dot-path → `trial.suggest_*` mapping |

Everything under `optimize/` is pure and optimizer-library-agnostic — swapping
the optimization library touches only `services/optuna/`.

Caching note: the HMM fit cache should be **disabled** (`cache.hmm: false` or
`--no-hmm-cache`) when a study searches HMM params, since each trial would get a
fresh config hash and never hit.

CLI, resumable studies, parallel workers, constraints: see
[../optimize.md](../optimize.md).
</content>
