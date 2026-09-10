"""Interleave matched CLI workloads; do not mix profiler runs into overhead baselines.

Example (from repository root):
    uv run python scripts/benchmark_backtest_performance.py -- \
        --start 2023-10-02 --end 2023-10-02

DATABASE_URL may select a disposable migrated database. Uses the current cache
without clearing it. Each invocation persists a normal research run. Results
include commands and whole-command wall times, never database credentials.
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("logs/performance-benchmark"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("backtest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error("use at least three repeats")
    extra = args.backtest_args
    if extra[:1] == ["--"]:
        extra = extra[1:]
    if any(
        arg.startswith(("--performance", "--no-performance", "--profile-output")) for arg in extra
    ):
        parser.error("benchmark owns performance/profile flags")
    args.output.mkdir(parents=True, exist_ok=True)
    modes = [
        ("off", ["--no-performance"]),
        ("final", ["--performance-interval", "0"]),
        ("periodic", ["--performance-interval", "10"]),
    ]
    results = []
    for repeat in range(args.repeats):
        for name, flags in modes[repeat % 3 :] + modes[: repeat % 3]:
            command = [sys.executable, "-m", "carcharoth", "backtest", *extra, *flags]
            log_path = args.output / f"{repeat}-{name}.log"
            start = time.perf_counter()
            with log_path.open("w") as log:
                completed = subprocess.run(
                    command, stdout=log, stderr=subprocess.STDOUT, check=False
                )
            elapsed = time.perf_counter() - start
            result = {
                "repeat": repeat,
                "mode": name,
                "wall_seconds": elapsed,
                "returncode": completed.returncode,
                "command": command,
                "log": str(log_path),
            }
            results.append(result)
            (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(f"{repeat} {name}: {elapsed:.3f}s (exit {completed.returncode})", flush=True)
            if completed.returncode:
                raise SystemExit(f"Benchmark stopped: inspect {log_path}")
    medians = {
        name: statistics.median(r["wall_seconds"] for r in results if r["mode"] == name)
        for name, _ in modes
    }
    print(
        json.dumps(
            {
                "median_seconds": medians,
                "overhead_percent": {
                    name: 100 * (elapsed / medians["off"] - 1) for name, elapsed in medians.items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
