# Ideas Backlog: Mean Reversion Optimization

## Current Status
- **Baseline**: fitness=-0.859, sharpe=-0.849, total_return=-0.16%, profit_factor=0.853, win_rate=55.9%
- **Goal**: Push fitness > 0.0 (positive territory) while maintaining reasonable risk.

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

(None yet; this section will be populated as experiments fail.)

## Promising Paths

(Will update as experiments succeed.)
