# Regime Detection

`regime/` classifies each symbol's market regime from its bars, behind the
`RegimeDetector` ABC (`interfaces/regime_detector.py`). `regime/detectors.py`
(`build_detector`) is the config → concrete detector seam, mirroring
`strategies/registry.py`.

> **Diagram:** [`regime-detection-overview.mmd`](diagrams/regime-detection-overview.mmd) — render with `mmdc -i diagrams/regime-detection-overview.mmd -o regime-detection-overview.svg`

The consumer (`RegimeStrategyProvider`, see
[strategy-provider.md](strategy-provider.md)) only ever sees the ABC.

## Score detector

Evidence-based: weighted feature scores on a trend ↔ mean-reversion axis,
attenuated by change-detection (stability) evidence. Features are pluggable via
`regime/registry.py`.

> **Diagram:** [`regime-detection-score-detector.mmd`](diagrams/regime-detection-score-detector.mmd) — render with `mmdc -i diagrams/regime-detection-score-detector.mmd -o regime-detection-score-detector.svg`

Add a feature: implement `RegimeFeature` in `regime/features/`, register in
`regime/registry.py`, list it under `regime.score.features` in config.

## HMM detector

A per-symbol Gaussian hidden Markov model (hmmlearn) over a 4-D observation
vector, lazily fitted and periodically refit. Hidden states are labelled from
their emission means, so the *meaning* of a state is derived, not hardcoded.

> **Diagram:** [`regime-detection-hmm-detector.mmd`](diagrams/regime-detection-hmm-detector.mmd) — render with `mmdc -i diagrams/regime-detection-hmm-detector.mmd -o regime-detection-hmm-detector.svg`

### HMM fit cache keys

`HmmFitCache` (optional, injected via `build_detector(..., hmm_fit_store=...)`)
lives in Redis and is keyed by:

```
carch:hmm:v1:{config_hash}:{symbol}:{obs_hash}
```

- `config_hash` covers every **fit-relevant** field plus the hmmlearn version.
  `evaluate_interval_minutes` and `min_confidence` are deliberately excluded —
  they don't change fit output.
- `obs_hash` covers the exact training matrix.

Because fitting is seed-deterministic, a cached fit is bit-identical to a fresh
one — safe to reuse across runs, studies and `--workers N` processes. Disable
with `cache.hmm: false` / `--no-hmm-cache` when a study searches HMM params
(every trial would otherwise get a fresh `config_hash` and never hit).

## Models

`regime/models.py` defines `Regime` (six values), `Evidence`,
`RegimeAssessment` (with optional `probabilities`), and `StrategyAssignment`.
`EMITTED_REGIMES` in `detectors.py` records which regimes each detector can
emit, so config that maps an unreachable regime gets a startup warning.
</content>
