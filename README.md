![Carcharoth](assets/banner.png)

# Carcharoth

Model-driven intraday stock trading and research platform. Live execution uses an Alpaca paper
account; training, quicktests, backtests, permutation tests, and Optuna studies reuse the same
feature schema and immutable XGBoost artifacts.

> Paper trading and experimentation only. This is not financial advice.

## Architecture

```text
Scheduler / BacktestRunner
  -> TradingEngine.tick()
     0. reconcile fills
     1. MarketDataService: completed bars + quotes for tradable symbols and SPY context
     2. AccountService: equity, buying power, signed positions
     3. ModelSignalGenerator
          FeaturePipeline -> XGBoostPredictor -> DecisionPolicy
          LONG | SHORT | FLAT | UNCHANGED
     4. RiskManager: validate and size the target transition
     5. OrderExecutor: submit the approved order
```

The engine only orchestrates typed modules. Live and replay differ through injected market-data,
account, and execution adapters. An order decided from completed bar `t` fills at the next bar's
open in replay; live submits a market order after the completed bar. Reversals close first and
open the opposite direction only after the account confirms flat. Intraday safety always flattens
before the close.

Offline training is deliberately separate:

```text
Historical BarsFetcher (global universe + SPY)
  -> shared FeaturePipeline
  +  regression/classification TargetBuilder
  -> chronological train/validation datasets
  -> XGBoostTrainer
  -> artifacts/models/<id>/{model.ubj,manifest.json,report.json}
```

Artifacts are immutable and never activated automatically. Set `model.artifact_path` in a
research profile, verify it out of sample, then promote that config leaf to `trading/paper`.
The old rule-based strategies and regime routing do not exist. Regime-detector source remains
disconnected under `src/carcharoth/regime/` for possible future model context.

## Initial features

The ordered feature list lives in `config/base.yaml`; changing it changes the schema hash and
requires retraining.

- momentum: 1/5/20/60-bar log returns
- trend: 20/60-bar EMA distance and ADX(14)
- volatility: normalized ATR(14), 20/60-bar realized volatility
- price position: normalized RSI(14), 20-bar range position, session VWAP distance
- volume: same-time-of-day log-volume z-score over 20 prior sessions
- context: SPY 5/20-bar returns and cyclical time-of-day

Features are scale-free and do not include symbol IDs, regime, raw prices, or quote fields that
cannot be reproduced from historical bars. `report.json` records constant/high-correlation
checks and gain importance.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Docker, and on macOS the OpenMP runtime used by
XGBoost.

```bash
brew install libomp              # macOS only
uv sync
cp .env.example .env             # add Alpaca paper credentials
docker compose up -d
uv run alembic upgrade head
```

## Workflow

```bash
# 1. Train a global regression artifact. Date ranges come from the profile.
uv run carcharoth train -p training

# Optional date overrides; all are recorded in config provenance.
uv run carcharoth train -p training \
  --train-start 2023-01-01 --train-end 2024-06-30 \
  --validation-start 2024-07-01 --validation-end 2024-12-31

# 2. Explicitly select the printed artifact for an out-of-sample research run.
uv run carcharoth quicktest --set model.artifact_path=artifacts/models/<id>
uv run carcharoth backtest --set model.artifact_path=artifacts/models/<id>

# 3. Optimize decision thresholds/risk without retraining the artifact.
uv run carcharoth optimize --set model.artifact_path=artifacts/models/<id>

# 4. After review, use the TUI Promote screen to promote model.artifact_path.
cargo run -p carcharoth-tui
uv run carcharoth run            # Alpaca paper trading
```

A backtest/quicktest window must start strictly after the artifact's validation window. Runtime
rejects missing/corrupt artifacts, model hashes, task/schema/config mismatches, and in-sample
windows.

## CLI

| Command | Purpose |
|---|---|
| `carcharoth train` | Build and publish an immutable global XGBoost artifact |
| `carcharoth run` | Live Alpaca paper trading with an explicitly selected artifact |
| `carcharoth quicktest` | Fast per-symbol, fixed-artifact long/short simulation |
| `carcharoth backtest` | Replay the full engine, risk, signed broker, and next-open fills |
| `carcharoth optimize` | Optuna search over fixed-artifact decision/risk settings |
| `carcharoth quicktest --permute [METHOD]` | Bar permutation or trade Monte Carlo |
| `carcharoth backtest --permute [METHOD]` | Trade-based Monte Carlo on a finished run |
| `carcharoth analyze --run-id UUID` | Recompute metrics and signed FIFO round trips |
| `carcharoth config ...` | List, resolve, validate, diff, hash, or emit schema |
| `carcharoth cache stats\|clear` | Inspect or clear cached historical bars |

The Rust TUI is a front-end only. It invokes `uv run carcharoth ...`, edits temporary `--set`
overrides, and promotes selected leaves into `config/generated/paper-promoted.yaml`.

## Configuration

`config/base.yaml` owns the strict structure. Profiles recursively extend layers; mappings
merge, lists replace, and later layers win. `model.target` and `optimization.search_space` are
atomic mappings. Every run stores the validated config, content hash, and provenance.

Important sections:

- `model`: artifact path, timeframe, benchmark, ordered features, regression/classification
  target, decision thresholds/session policy, and XGBoost parameters
- `training`: chronological train/validation dates, artifact root, seed, early stopping
- `data`: out-of-sample quicktest/backtest/Optuna window
- `risk`: symmetric position sizing, gross-exposure, buying-power, position-count, daily loss
- `backtest`: initial capital, margin multiplier, synthetic spread/slippage
- `optimization`: fixed-artifact threshold/risk search space and constraints

Optuna rejects search paths under features, target, timeframe, benchmark, XGBoost parameters,
artifact path, or training config because those require a retrained artifact.

## Persistence and monitoring

PostgreSQL stores run-scoped `model_decisions`, orders, fills, signed position/equity snapshots,
long/short `round_trips`, metrics, and permutation results. Slim backtests omit high-volume model
features unless `--verbose-db` is used. Grafana dashboards live under
`grafana/provisioning/dashboards/`.

## Development

```bash
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy src
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

See `.claude/architecture.md` and `.claude/` for module and workflow details.
