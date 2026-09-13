# Quicktest

Quicktest is a fast, independent per-symbol evaluation of one fixed model artifact. It skips the
portfolio engine/risk manager but uses the same feature pipeline, predictor, decision policy,
SPY context, session rules, signed positions, and next-open fill semantics.

```bash
uv run carcharoth quicktest --set model.artifact_path=artifacts/models/<id>
uv run carcharoth quicktest --permute in_sample_bars --workers 4 \
  --set model.artifact_path=artifacts/models/<id>
```

`--set model.prepared_history=true` prepares targets once before the symbol loop, and
prepares each permutation from its transformed bars using a fresh model. Default false;
direct `simulate_symbol()` and live callers keep rolling computation. Quicktest slices raw
bars before session filtering, so sparse/extended-hours availability may differ from batch
and full replay. Treat this as a compatibility audit, not assumed parity.

Each symbol has independent capital so portfolio contention does not hide symbol-level edge.
Results persist once at the end and reuse run/trade/equity/round-trip/metric tables. The artifact
must be out of sample for the quicktest window.

Bar permutation destroys serial structure and re-runs the same immutable artifact; it never
retrains. Trade Monte Carlo resamples/shuffles finished signed round trips and reports path-risk
distributions.
