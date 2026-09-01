# Optimization Flow

Optuna remains behind `ParameterOptimizer`. Each trial applies strict dot-path overrides, runs a
normal persisted fixed-artifact backtest, and maximizes its stored named fitness metric. Trial to
run linkage remains in Optuna user attributes.

Initial search paths are limited to `model.decision.*`, `risk.*`, and compatible replay settings.
Feature membership/params, target/horizon, timeframe, benchmark, XGBoost params, training dates,
and artifact path are rejected because changing them requires a new trained artifact. A future
training optimizer can reuse dataset/trainer interfaces without changing this backtest adapter.

Historical bars are cached across trials/processes. Invalid config combinations fail one trial;
metric constraints penalize the study value without changing the run's honest persisted fitness.
