#!/bin/bash
set -euo pipefail

# Carcharoth backtest benchmark script.
# Runs a 6-month backtest (Jan 1 – Jun 30, 2025) on the current config
# and outputs structured METRIC lines.

# Source venv
source "$(dirname "$0")/../.venv/bin/activate"

cd "$(dirname "$0")/.."

# Run the backtest and capture output
BACKTEST_OUTPUT=$(python -m carcharoth backtest \
  --start 2025-01-01 \
  --end 2025-06-30 \
  2>&1)

# Extract the run_id from the output
# Format: "run_id:  <uuid>"
RUN_ID=$(echo "$BACKTEST_OUTPUT" | grep -o 'run_id: [a-f0-9-]*' | awk '{print $NF}')

if [ -z "$RUN_ID" ]; then
    echo "ERROR: Could not extract run_id from backtest output" >&2
    echo "Output tail:" >&2
    echo "$BACKTEST_OUTPUT" | tail -30 >&2
    exit 1
fi

# Read the summary YAML file
SUMMARY_FILE="logs/backtests/${RUN_ID}.yaml"

if [ ! -f "$SUMMARY_FILE" ]; then
    echo "ERROR: Summary file not found: $SUMMARY_FILE" >&2
    exit 1
fi

# Use Python to parse the YAML and extract metrics
python3 << 'PYTHON_EOF'
import yaml
import sys

summary_file = sys.argv[1]

try:
    with open(summary_file, 'r') as f:
        summary = yaml.safe_load(f)
except Exception as e:
    print(f"ERROR: Could not read {summary_file}: {e}", file=sys.stderr)
    sys.exit(1)

if summary is None:
    print(f"ERROR: Summary YAML is empty", file=sys.stderr)
    sys.exit(1)

# Extract metrics from the summary
results = summary.get('results', {})
fitness_dict = summary.get('fitness', {})

# Collect metrics
metrics = {}

# Direct metrics from results (exclude per_symbol)
for key in ['sharpe', 'total_return', 'profit_factor', 'win_rate', 'max_drawdown', 'num_trades', 'avg_win', 'avg_loss']:
    if key in results and key != 'per_symbol':
        metrics[key] = results[key]

# Fitness metrics (these are the most important)
for objective_name, fitness_value in fitness_dict.items():
    metrics[f'fitness_{objective_name}'] = fitness_value

# Output metrics in METRIC format
if not metrics:
    print("WARNING: No metrics found in summary file", file=sys.stderr)
    print(f"Results keys: {list(results.keys())}", file=sys.stderr)
    print(f"Fitness keys: {list(fitness_dict.keys())}", file=sys.stderr)
    sys.exit(1)

# Output primary metric first (fitness_default), then secondaries
output_order = ['fitness_default', 'sharpe', 'total_return', 'profit_factor', 'win_rate', 'max_drawdown']
for key in output_order:
    if key in metrics:
        value = metrics[key]
        if value is not None:
            print(f"METRIC {key}={value}")

# Output any remaining metrics not in the order
remaining = set(metrics.keys()) - set(output_order)
for key in sorted(remaining):
    value = metrics[key]
    if value is not None:
        print(f"METRIC {key}={value}")

PYTHON_EOF "$SUMMARY_FILE"
