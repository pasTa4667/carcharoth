# Autoresearch: Improve Mean Reversion Strategy

## Objective

Optimize the intraday mean reversion strategy by iteratively improving key parameters and thresholds. The goal is to maximize **fitness** (weighted composite of Sharpe ratio + total return - max drawdown) while maintaining positive expectancy and reasonable risk.

Current baseline (master branch at 83e0f6a):
- **Fitness**: -13.54
- **Sharpe**: -13.46
- **Total Return**: -3.22%
- **Profit Factor**: 0.45 (losing $2.25 per $1 won)
- **Win Rate**: 45.9%
- **Max Drawdown**: 47.0%

## Metrics

- **Primary**: `fitness_default` (unitless, higher is better) — composite score from `config.yaml` objectives (Sharpe + 0.5×total_return - 2.0×max_drawdown)
- **Secondary**:
  - `sharpe` (ratio, higher is better)
  - `total_return` (%, higher is better)
  - `profit_factor` (ratio, higher is better)
  - `win_rate` (%, higher is better)
  - `max_drawdown` (%, lower is better)

## How to Run

```bash
source .venv/bin/activate
./.auto/measure.sh
```

This runs a 6-month backtest (Jan 1 – Jun 30, 2025) on the current config and outputs structured metrics.

## Files in Scope

### Strategy Parameters (`config/config.yaml` under `strategies.mean_reversion.params`)

- **`lookback`** (int, default 19) — bars used for rolling mean/std calculation. Controls sensitivity to recent price levels.
- **`entry_z`** (float, default -1.2) — z-score threshold to trigger a buy. More negative = waits for deeper dips. Range: -3.0 to 0.0.
- **`exit_z`** (float, default -0.5) — z-score threshold to sell. Must be > entry_z. Range: -1.0 to 0.5.
- **`trend_ema_period`** (int, default 195) — EMA period for trend filter. Entries only when rolling mean > EMA (uptrend gate). Range: 50 to 500.
- **`rsi_period`** (int, default 19) — RSI lookback period. Range: 7 to 30.
- **`rsi_entry_max`** (float, default 30.0) — maximum RSI to enter (oversold confirmation). Range: 20.0 to 50.0.
- **`atr_period`** (int, default 12) — ATR lookback period for stop loss. Range: 10 to 20.
- **`atr_stop_multiplier`** (float, default 2.1419…) — stop loss distance in ATRs below avg entry price. Range: 1.5 to 3.5.

### Risk/Backtest Parameters (also in `config.yaml`)

- **`max_position_notional`** (float, default 1000.0) — per-position hard cap. Affects leverage.
- **`max_position_pct_equity`** (float, default 0.116…) — per-position cap as % of equity.
- **`max_open_positions`** (int, default 8) — max concurrent positions.
- **`atr_stop_multiplier`** (under mean_reversion params) — controls risk/reward.

## Off Limits

- **Cannot change timeframe** (fixed at 5-minute intraday).
- **Cannot change market** (watchlist of 50 symbols is fixed).
- **Cannot redesign the strategy** (must stay mean reversion + end-of-day discipline).
- **Cannot add custom indicators or machine learning**.
- **Cannot introduce repainting or lookahead bias**.
- **Cannot remove risk management** (stops, flattenings, position caps).
- **Must maintain < 50% max drawdown** for stability.
- **Cannot use per-symbol config values** (all params are global across watchlist).
- **Cannot change `entry_delay_minutes` or `entry_cutoff_minutes`** (end-of-day gating is fixed).
- **Cannot change backtest timeframe** (always Jan 1 – Jun 30, 2025, unless explicitly overridden).

## Constraints

- **Max 2 parameters per iteration** — simplicity and signal clarity.
- **Backtests must complete successfully** (full 6-month data, all 50 symbols).
- **Simpler improvements are preferred** over complex tuning.
- **Changes must be evidence-driven** — address specific weaknesses (low win rate, high drawdown, negative Sharpe, etc.).
- **No test failures** — `pytest` must pass.

## What's Been Tried

- **Baseline (83e0f6a)**: entry_z=-1.2, exit_z=-0.5, lookback=19, trend_ema=195, rsi_max=30, atr_stop=2.14, atr_period=12
  - Fitness: -13.54, Sharpe: -13.46, Total Return: -3.22%, Win Rate: 45.9%, Max Drawdown: 47.0%
  - **Issue**: Severely negative Sharpe, losing money, very low profit factor.
  - **Root cause**: Entry thresholds (entry_z=-1.2) may be too aggressive, picking up many false reversals. RSI filter (max=30) may be too strict. Stop loss (atr_stop=2.14) may be too tight, getting whipped out on volatility.

## Next Steps for the Agent

1. Analyze the baseline: Why is Sharpe so negative? Is it:
   - Too many bad entries (entry_z too aggressive, or RSI filter too permissive)?
   - Stops too tight (atr_stop_multiplier too low)?
   - Exits too late (exit_z not tight enough)?
   
2. **Experiment 1**: Tighten entry threshold (entry_z more negative, e.g., -1.5 or -2.0) to reduce false positives. Monitor win rate and profit factor.

3. **Experiment 2**: If Sharpe improves, try loosening stop loss (atr_stop_multiplier higher, e.g., 2.5–3.0) to avoid whipsaws.

4. **Experiment 3**: If win rate is still low, tune RSI or lookback to improve entry quality.

5. **Experiment 4**: Once fitness > -5, focus on: trading fewer symbols, tighter risk caps, or trend filter tuning to improve consistency.

6. Keep secondary metrics in focus: profit factor and win rate are key to understand trade quality.

## Session Rules

- **Run backtests sequentially.** Each `measure.sh` invocation: run config → extract metrics.
- **Log every result** with `log_experiment` immediately after the backtest. Include ASI notes on what broke/worked.
- **Confidence score** (reported by `log_experiment`): ≥2.0× means likely real improvement; <1.0× is noise.
- **Never discard a win** — if fitness improves, `keep`.
- **If fitness regresses**, revert with `discard`.
- **Avoid thrashing**: don't flip the same two parameters back and forth. Once a dead end is found, try something structurally different.
- **When stuck**, re-read the strategy code and backtest output. Look for clues: which symbols win? Which lose? Are stops being hit constantly?
