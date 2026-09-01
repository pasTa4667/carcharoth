# Regime Detection (Disconnected)

`src/carcharoth/regime/` retains two independently tested detector implementations for possible
future use as model context:

- score detector over Hurst, volatility clustering, CUSUM, and Wasserstein evidence
- per-symbol Gaussian HMM over return, rolling volatility, EMA distance, and ADX

Neither detector is present in `RunConfig`, constructed by `main.py`, called by the engine,
persisted during runs, or included in the current XGBoost feature schema. There is no
regime-to-strategy router or assignment state. `RegimeDetector` and detector-specific config/
cache helpers remain so a later feature can integrate the output through `FeaturePipeline`
without restoring legacy strategy routing.
