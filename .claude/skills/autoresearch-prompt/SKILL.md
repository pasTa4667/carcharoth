---
name: autoresearch-prompt
summary: Write a filled Autoresearch prompt.md for a strategy against a fixed quicktest benchmark.
description: Use when asked to set up Autoresearch, write .auto/prompt.md, autonomously experiment on a trading strategy, find a robust strategy edge, or run strategy autoresearch.
disable-model-invocation: true
---

# Autoresearch: write `.auto/prompt.md`

Autoresearch reads `.auto/prompt.md`. This skill's job is to create that file.
1. Ask for the strategy to fill in for `<STRATEGY_NAME>` and the .
2. Infer from the given strategy or ask for every `<PLACEHOLDER>` in the template below for the strategy and benchmark being studied, unless they can be infered by the strategy_name placeholder.
3. Write the filled template to `.auto/prompt.md` (create `.auto/` if needed). Write it even though `.auto/` is gitignored.
4. Replace every placeholder with concrete values. Leave no `<PLACEHOLDER>` tokens in the written file.
5. Keep this skill free of experiment findings, prior-run metrics, and run logs so it remains reusable.

Do not run the experiment loop here. After `prompt.md` is written, Autoresearch uses that file.

## Prompt template

Copy the content below into `.auto/prompt.md`, with placeholders filled:

# Autoresearch: improve a strategy with a robust edge

## Objective
Redesign the **logic** of `<STRATEGY_NAME>` so it has a stable, positive edge across `<UNIVERSE_DESCRIPTION>`. Optimize for a robust, consistent edge rather than peak profit on one period or symbol.

Create any shell scripts needed to measure the strategy. Fills, capital allocation, and metric definitions are fixed by the benchmark configuration. Do not change them during the loop.

Success target:
- Primary objective: `fitness_default` must improve and reach `<PRIMARY_SUCCESS_CRITERION>`.
- Robustness: `<ROBUSTNESS_CRITERION>`, such as positive results in each sub-window and broad per-symbol support.
- Trade/exposure floor: `num_trades` at or above 250.
- Risk ceiling: `max_drawdown` at or below 0.50.

## Metrics
- **Primary:** `fitness_default` (higher is better).
- **Secondary constraint:** `num_trades` must remain at or above 250.
- **Risk constraint:** `max_drawdown` must remain at or below 0.50.
- **Diagnostic metrics:** Sharpe, total return, drawdown, win rate, profit factor, and per-symbol results.

Inspect `<RESULTS_PATH>` after promising runs to verify that improvement is broad rather than driven by a small number of symbols, trades, or outliers.

## How to measure
Create the shell scripts needed to run the benchmark and capture metrics. Scripts must emit machine-readable `METRIC name=value` lines. Record the baseline before making changes. Treat repeated results as comparable only when the data, window, configuration, and measurement remain fixed.

If the measurement has robustness sub-windows, require all of these to remain healthy:
- `<SUBWINDOW_METRIC_1>` — `<SUBWINDOW_1_DESCRIPTION>`
- `<SUBWINDOW_METRIC_2>` — `<SUBWINDOW_2_DESCRIPTION>`
- `<ADDITIONAL_ROBUSTNESS_CHECKS>`

## Files in scope
Only modify strategy implementation and directly related, pure strategy helpers:

- `<STRATEGY_FILE>`
- `<INDICATOR_HELPER_FILES>`
- `<FILTER_OR_SESSION_HELPER_FILES>`

Preserve the strategy interface and configuration compatibility. New parameters must have defaults unless the benchmark configuration is intentionally updated outside this experiment.

## Off limits
- Do **not** change `<FIXED_BENCHMARK_CONFIGS>` (window, symbols, sizing, fills, or objective weights).
- Do **not** modify `<OUT_OF_SCOPE_COMPONENTS>`, such as engine, risk management, persistence, backtest framework, or optimizer infrastructure.
- Run only `<ALLOWED_COMMANDS>` during the loop; do not run `<DISALLOWED_COMMANDS>`.

## Constraints
- Optimize implementation and decision logic, not benchmark configuration or objective weights.
- Discard a run that violates the trade/exposure floor or risk ceiling.
- Avoid parameter sweeps disguised as structural work. Numeric defaults may change when justified by a logic change, but seek a genuine, explainable edge.
- Add no heavy dependencies; use only `<ALLOWED_DEPENDENCIES>`.
- Keep the per-bar evaluation path fast.
- `<TESTING_POLICY>`.

## Experiment loop
1. Read the current implementation, benchmark configuration, and metric definition. Measure and record a baseline.
2. Form one falsifiable hypothesis about market behavior or a strategy failure mode.
3. Make one coherent, minimal implementation change that tests that hypothesis.
4. Run the measurement scripts and capture all reported metrics.
5. Reject and revert changes that fail the primary metric, constraints, or robustness checks. Keep only improvements that are explainable and robust.
6. For promising changes, inspect per-symbol results and sub-windows. Check that gains are not concentrated in a few names, a handful of trades, or one sub-period.
7. Repeat. Do not combine unrelated changes in one experiment; preserve a clean best-known implementation throughout.

## Edge-versus-overfit discipline
- The primary metric ranks candidates, but robustness decides whether to keep them.
- Prefer changes that improve multiple periods and many symbols, with healthy risk-adjusted returns, over a large aggregate spike from limited exposure.
- Do not game the objective by collapsing trade count, exposure, or downside risk through inactivity.
- A deterministic benchmark makes comparisons reproducible, not automatically generalizable. Use genuinely held-out periods or temporary out-of-window checks for validation.
- If temporarily changing a benchmark window for validation, restore the canonical configuration immediately afterward. Never compare a validation result to the canonical benchmark as if they were the same test.
- Keep a separate, append-only experiment log (for example `.auto/ideas.md` or `.auto/log.jsonl`) for hypotheses, outcomes, and rejected approaches. Do not put that run-specific history in this reusable skill.

## Completion criteria
Finish when `<COMPLETION_CRITERIA>`. Report the retained implementation, baseline versus final metrics, robustness evidence, and remaining limitations. Do not claim a stable edge unless the required sub-windows, trade/exposure floor, risk limit, and breadth checks all pass.
