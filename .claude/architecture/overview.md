# Module Overview

```mermaid
flowchart TB
  MAIN[main.py composition root] --> ENG[engine]
  ENG --> IF[interfaces + domain]
  ENG --> MODEL[modeling]
  MODEL --> FEAT[features]
  MODEL --> XGB[services/xgboost]
  ENG --> RISK[risk]
  ENG --> BROKERS[services/alpaca + services/backtest]
  ENG --> DB[persistence]
  BACK[backtest / quicktest / permutation] --> ENG
  OPT[services/optuna] --> BACK
  REG[regime detectors retained, disconnected]:::off
  classDef off stroke-dasharray: 5 5
```

Training and runtime share `FeaturePipeline`; runtime never builds labels and training never
places orders. Provider SDK types stay in provider adapters. See `../architecture.md` for the
complete contracts and invariants.
