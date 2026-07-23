# Autoresearch: Improve Mean Reversion Strategy

## Objective

Optimize the intraday mean reversion strategy by iteratively improving key parameters and thresholds. The goal is to maximize **fitness** (weighted composite of Sharpe ratio + total return - max drawdown) while maintaining positive expectancy and reasonable risk. Focus on structural changes, not config changes, meaning adding/removing filters changing stop/loss behavior, different entry/exit conditions, etc.

Current baseline (autoresearch/improve-mean-reversion-2026-07-22 at cec59db):
- **Fitness**: -0.859
- **Sharpe**: -0.849
- **Total Return**: -0.16%
- **Profit Factor**: 0.853
- **Win Rate**: 55.9%
- **Max Drawdown**: 0.44% (excellent!)

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

- **Max 2 items per iteration** — simplicity and signal clarity.
- **Backtests must complete successfully** (full 6-month data, all 50 symbols).
- **Simpler improvements are preferred** over complex tuning.
- **Changes must be evidence-driven** — address specific weaknesses (low win rate, high drawdown, negative Sharpe, etc.).
- **No test failures** — `pytest` must pass.

## What's Been Tried

- **Baseline (cec59db)**: entry_z=-1.2, exit_z=-0.5, lookback=19, trend_ema=195, rsi_max=30, atr_stop=2.14, atr_period=12
  - Fitness: -0.859, Sharpe: -0.849, Total Return: -0.16%, Win Rate: 55.9%, Max Drawdown: 0.44%, Profit Factor: 0.853
  - **Status**: Much better than historical baseline! But still negative Sharpe and slightly negative return.
  - **Next steps**: Try tightening entry threshold to reduce false positives, or loosening stop loss to avoid whipsaws.

## Optimization Strategy

1. **Fitness is slightly negative (-0.859)**: small negative return (-0.16%) with low Sharpe (-0.849).
   - Max drawdown is excellent (0.44%), so risk management is working.
   - Profit factor 0.853 means slightly more losses than wins; need to improve entry quality.

2. **Priority 1**: Tighten entry z-score (e.g., -1.5, -1.8, -2.0) to reduce false positives.
   - Hypothesis: entry_z=-1.2 is too shallow; catching many mean reversals that don't actually reverse.
   - Expected impact: Fewer trades, but higher quality → better Sharpe, higher profit factor.
   - Watch for: Win rate and profit factor improvement.

3. **Priority 2** (if entry tightening doesn't help): Loosen stop loss (atr_stop to 2.5–3.0).
   - Hypothesis: Current 2.14 is too tight; getting stopped out on normal volatility before price reverts.
   - Expected impact: Longer hold times, fewer whipsaws, potentially larger wins.

4. **Priority 3**: Adjust RSI or lookback if entry quality is still suboptimal.

5. **Goal**: Push fitness into positive territory (> 0.0) while keeping max_drawdown < 50%.

## Session Rules

- **Run backtests sequentially.** Each `measure.sh` invocation: run config → extract metrics.
- **Log every result** with `log_experiment` immediately after the backtest. Include ASI notes on what broke/worked.
- **Confidence score** (reported by `log_experiment`): ≥2.0× means likely real improvement; <1.0× is noise.
- **Never discard a win** — if fitness improves, `keep`.
- **If fitness regresses**, revert with `discard`.
- **Avoid thrashing**: don't flip the same two parameters back and forth. Once a dead end is found, try something structurally different.
- **When stuck**, re-read the strategy code and backtest output. Look for clues: which symbols win? Which lose? Are stops being hit constantly?
