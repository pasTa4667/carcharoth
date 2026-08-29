# Long/Short Decisioning

Regime detection and trade direction are separate decisions. The
`StrategyProvider` first selects one strategy **family** for the symbol. While
flat, that strategy computes normalized `EntryCandidate`s for long and short
and passes them to the pure `DirectionalArbiter`.

Candidate scores are strategy-family heuristics in `[0, 1]`, not probabilities:
`0` is ineligible, `0.5` just clears the setup threshold, and `1` is saturated
strength. Raw strategy indicators remain diagnostics and are never compared.
The arbiter returns no entry unless the winner clears `directional.min_entry_score`
and leads by `directional.min_score_margin`. `shorting_enabled: false` makes the
short candidate ineligible.

Once positioned, arbitration is bypassed. Signed quantity determines direction
(`qty > 0` long, `qty < 0` short), and the entering family evaluates only that
direction's exit. A regime change remains pending until flat. An opposite setup
therefore cannot reverse a position in one tick: close first, reconsider later.

## Intent and broker side

Strategies emit explicit intents:

| Intent | Broker side | Meaning |
|---|---|---|
| `OPEN_LONG` | buy | enter long |
| `CLOSE_LONG` | sell | flatten long |
| `OPEN_SHORT` | sell | enter short |
| `CLOSE_SHORT` | buy | flatten short |
| `HOLD` | none | no order |

Risk sizes entries using gross exposure and a separate short cap. Closes bypass
entry lockouts and close `abs(position.qty)`. Alpaca sell-to-open checks the
asset's `tradable` and `shortable` flags before submission. The simulators use
signed holdings, margin buying power, direction-aware spread/slippage and
configurable borrow APR.

## Strategy rules

- `mean_reversion`: long buys negative-z dips in an uptrend; short sells
  positive-z rallies in a downtrend. Each direction has its own reversion and
  adverse-stop exits, plus direction-aware ATR/EOD exits.
- `ema_vwap`: long requires a fresh bullish EMA cross above VWAP; short mirrors
  it with a fresh bearish cross below VWAP. EMA reversal, ATR and EOD manage
  exits.

Orders/trades persist intent and round trips persist direction and fees. Analysis
FIFO-matches both directions and reports aggregate plus long/short attribution.
Shorting remains disabled in `config/base.yaml`; enable it only in a research
profile, validate out of sample, then explicitly promote it to paper trading.
