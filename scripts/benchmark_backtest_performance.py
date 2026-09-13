"""Interleave matched CLI workloads; do not mix profiler runs into overhead baselines.

Example (from repository root):
    uv run python scripts/benchmark_backtest_performance.py -- \
        --start 2023-10-02 --end 2023-10-02

DATABASE_URL should select a disposable migrated database. Uses the current cache
without clearing it unless --isolated-cache explicitly authorizes v2 cold fills.
Each invocation persists a normal research run. Results include commands and
whole-command wall times, never database credentials.
"""

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("logs/performance-benchmark"))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--mode", choices=("overhead", "p0"), default="overhead")
    parser.add_argument(
        "--baseline-root",
        type=Path,
        help="preserved v1 checkout with its own .venv; required for P0",
    )
    parser.add_argument("--input-fingerprint", help="SHA256 of the frozen fetched input")
    parser.add_argument(
        "--isolated-cache",
        action="store_true",
        help="authorize v2 clears for cold-fill runs; use a disposable REDIS_URL",
    )
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
    if args.mode == "p0" and not (args.baseline_root and args.input_fingerprint):
        parser.error("P0 requires --baseline-root and --input-fingerprint; freeze inputs first")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    modes = [
        ("off", ["--no-performance"]),
        ("final", ["--performance-interval", "0"]),
        ("periodic", ["--performance-interval", "10"]),
    ]
    if args.mode == "p0":
        modes = [
            ("v1-legacy", ["--performance-interval", "0"]),
            ("v2-legacy", ["--performance-interval", "0", "--set", "model.prepared_history=false"]),
            (
                "v2-prepared",
                ["--performance-interval", "0", "--set", "model.prepared_history=true"],
            ),
        ]
        if args.isolated_cache:
            modes.append(("v2-cold", modes[1][1]))
    results = []
    for repeat in range(args.repeats):
        rotation = repeat % len(modes)
        for name, flags in modes[rotation:] + modes[:rotation]:
            root = args.baseline_root.resolve() if name == "v1-legacy" else Path.cwd()
            python = str(root / ".venv/bin/python") if name == "v1-legacy" else sys.executable
            if name == "v2-cold":
                subprocess.run(
                    [python, "-m", "carcharoth", "cache", "clear", "--bars-version", "v2"],
                    cwd=root,
                    check=True,
                )
            command = [python, "-m", "carcharoth", "backtest", *extra, *flags]
            if args.mode == "p0" and name != "v2-cold":
                # Untimed priming also handles a cold mode immediately before this mode.
                with (args.output / f"{repeat}-{name}-prime.log").open("w") as prime_log:
                    subprocess.run(
                        command, cwd=root, stdout=prime_log, stderr=subprocess.STDOUT, check=True
                    )
            before = set((root / "logs/backtests").glob("*.performance.json"))
            rss_path = args.output / f"{repeat}-{name}.rss"
            measured = command
            if Path("/usr/bin/time").exists():
                options = ["-l"] if sys.platform == "darwin" else ["-v"]
                measured = ["/usr/bin/time", *options, "-o", str(rss_path), *command]
            log_path = args.output / f"{repeat}-{name}.log"
            start = time.perf_counter()
            with log_path.open("w") as log:
                completed = subprocess.run(
                    measured, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=False
                )
            elapsed = time.perf_counter() - start
            reports = sorted(set((root / "logs/backtests").glob("*.performance.json")) - before)
            rss_text = rss_path.read_text() if rss_path.exists() else ""
            rss_match = (
                re.search(r"(\d+)\s+maximum resident set size", rss_text)
                if sys.platform == "darwin"
                else re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", rss_text)
            )
            result = {
                "input_fingerprint": args.input_fingerprint,
                "peak_rss_bytes": (
                    int(rss_match[1]) * (1 if sys.platform == "darwin" else 1024)
                    if rss_match
                    else None
                ),
                "performance": [json.loads(path.read_text()) for path in reports],
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
                "range_seconds": {
                    name: {
                        "min": min(values),
                        "max": max(values),
                        "variance": statistics.variance(values),
                    }
                    for name, _ in modes
                    for values in [[r["wall_seconds"] for r in results if r["mode"] == name]]
                },
                "overhead_percent": {
                    name: 100 * (elapsed / medians["off"] - 1) for name, elapsed in medians.items()
                }
                if "off" in medians
                else {},
                "gate": "NOT EVALUATED: independently compare predictions, risk and trades",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
