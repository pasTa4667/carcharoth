# Ideas Backlog: Mean Reversion Optimization

## Current Status
- **BREAKTHROUGH**: fitness=+0.116, sharpe=+0.120, total_return=+0.020%, profit_factor=1.036, win_rate=55.1%, max_dd=0.235%
- **Previous Baseline**: fitness=-0.859, sharpe=-0.849, total_return=-0.16%, profit_factor=0.853, win_rate=55.9%
- **Improvement**: +0.975 fitness (breakthrough to positive!)
- **Goal**: ✅ ACHIEVED - Push fitness > 0.0 - Now exploring fine-tuning for higher Sharpe.

## High-Priority Ideas

### 1. Tighten Entry Z-Score Threshold (entry_z)
- **Hypothesis**: entry_z=-1.2 is too aggressive; catching false reversals.
- **Action**: Try entry_z=-1.5 or -2.0 to wait for deeper dips.
- **Expected Impact**: Fewer entries, higher quality, better win rate and profit factor.
- **Risk**: Fewer trades; if too tight, misses legitimate reversals.

### 2. Loosen Stop Loss (atr_stop_multiplier)
- **Hypothesis**: Current 2.14 is too tight; getting stopped out on normal volatility.
- **Action**: Try 2.5 or 3.0 to allow more room before exit.
- **Expected Impact**: Fewer whipsaws, longer hold times, potential larger wins.
- **Risk**: Larger losses if trade goes wrong; higher max drawdown.

### 3. Relax RSI Entry Filter (rsi_entry_max)
- **Hypothesis**: rsi_entry_max=30 is too strict; missing valid oversold conditions.
- **Action**: Try 35.0 or 40.0 to increase entry opportunities.
- **Expected Impact**: More trades, potentially better Sharpe if quality holds.
- **Risk**: More false entries if RSI isn't a strong signal.

### 4. Adjust Lookback Period (lookback)
- **Hypothesis**: lookback=19 might be too short (1 hour of 5-min bars); noise-sensitive.
- **Action**: Try 20-25 for smoother rolling mean/std.
- **Expected Impact**: More stable z-scores, fewer false signals.
- **Risk**: Slower to react to recent price changes.

### 5. Tighten Trend EMA (trend_ema_period)
- **Hypothesis**: trend_ema_period=195 (6.5 hours) is too long; lags price.
- **Action**: Try 100-150 for faster trend detection.
- **Expected Impact**: More responsive trend filter, more entry opportunities.
- **Risk**: More entries in actual downtrends if EMA lags.

## Medium-Priority Ideas

### 6. Exit Z-Score Adjustment (exit_z)
- **Hypothesis**: exit_z=-0.5 exits too early; leaving gains on the table.
- **Action**: Try -0.3 or 0.0 (mean) to hold longer.
- **Expected Impact**: Longer holds, larger average wins.
- **Risk**: Larger drawdowns if price overshoots mean.

### 7. RSI Period Adjustment (rsi_period)
- **Hypothesis**: rsi_period=19 might be suboptimal for 5-min bars.
- **Action**: Try 14 or 21 (more standard periods).
- **Expected Impact**: Potentially more stable RSI readings.
- **Risk**: Minimal; RSI is a secondary filter.

### 8. ATR Period Adjustment (atr_period)
- **Hypothesis**: atr_period=12 is reasonable, but could try 14 (standard).
- **Action**: Minor experiment; lower priority.

## Low-Priority Ideas

### 9. Risk Cap Adjustments
- Note: Can only tune within current structure; cannot add per-symbol limits.
- **max_open_positions**: Currently 8. Could try 6-10.
- **max_position_pct_equity**: Currently 0.116. Could try 0.10-0.12.

### 10. Complex Interactions
- Combinations of above: e.g., tighter entry_z + looser atr_stop + higher rsi_entry_max.
- Only pursue after single-variable wins.

## Dead Ends (Do Not Revisit)

1. **Tighten entry_z (-1.5)**: fitness=-1.021 (WORSE)
   - entry_z=-1.2 is already well-optimized; tightening caused missed reversals.

2. **Loosen entry_z with looser stops (entry_z=-1.4)**: fitness=-0.919 (WORSE)
   - Single parameter change doesn't help; need systematic approach.

3. **Loosen RSI filter (rsi_entry_max=35)**: fitness=-2.534 (CATASTROPHIC)
   - RSI at 35 allows too many false entries (833 trades!)
   - RSI filter is crucial quality gate; do not relax.

4. **Extend exit z-score (exit_z=0.0)**: fitness=-1.694 (MUCH WORSE)
   - Holding too long creates large drawdowns.
   - Current exit_z=-0.5 is optimal.

5. **Increase lookback (lookback=22)**: fitness=-0.888 (WORSE)
   - 19 bars is already a good balance; more lookback = less responsive.

## Promising Paths (WINNING COMBINATION) ✅

**BREAKTHROUGH ACHIEVED**: 
- **atr_stop_multiplier: 2.14 → 3.0** (critical!)
  - Loose stops allow positions to breathe through normal volatility.
  - Progression: 2.5 (+0.037) → 2.7 (+0.021) → 2.8 (+0.091) → 2.9 (+0.065) → 3.0 (+0.191)
  - Each increment improved fitness; 3.0 crosses into positive territory.

- **trend_ema_period: 195 → 120** (critical!)
  - Tighter trend filter produces higher quality entries.
  - Progression: 195 (baseline) → 150 (+0.377) → 120 (+0.185) → 100 (regression)
  - Sweet spot at 120.

**Combined Effect**: 
- Baseline: fitness=-0.859
- With 3.0 + 120: fitness=+0.116 (+++0.975 improvement)
- This is a multiplicative win, not additive.
