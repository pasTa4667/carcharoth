# Design Principles

## Interface-first, deep modules

External variation lives at small seams in `interfaces/`; concrete adapters are wired only in
`main.py`. The engine's model seam is `ModelSignalGenerator`: market requirements plus one
`generate` operation. Feature registry, artifact validation, predictor, decision policy, and
completed-bar state are internal implementation details.

## Type-first

Strict mypy is enabled. Domain dataclasses cross interfaces; SDK/native types remain in Alpaca,
XGBoost, Optuna, Redis, or SQLAlchemy adapters.

## Reproducible model schema

Features are ordered registry entries with stable output names, params, implementation versions,
and warm-up requirements. The schema fingerprint and native model hash are persisted in an
immutable artifact manifest and checked before runtime.

## Layered configuration

`config/base.yaml` owns the strict shape. Mappings merge recursively, lists replace, and later
layers win. `model.target` and `optimization.search_space` replace atomically. Run config hashes
and provenance make research replayable.

## One composition root

Live, replay, training, quicktest, and optimization construct concrete implementations in
`main.py`; modules accept dependencies rather than creating remote/native clients themselves.

## No legacy fallback

There is no rule-based strategy interface/provider, regime router, or automatic model fallback.
Missing inference yields `UNCHANGED`; a bad artifact fails startup.
