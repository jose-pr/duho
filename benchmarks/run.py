#!/usr/bin/env python3
"""Structured benchmark runner for duho.

Produces a comparable JSON result plus a human summary. Save a run to the
history with --save; results land in benchmarks/results/<name>.json where
<name> defaults to duho-<version>-py<major><minor>.

    python benchmarks/run.py            # print summary (warm metrics)
    python benchmarks/run.py --cold     # also run the cold (per-invocation) set
    python benchmarks/run.py --save     # write benchmarks/results/<name>.json
    python benchmarks/run.py --json PATH # write the metrics JSON to PATH
    python benchmarks/run.py --name foo # custom result name

Each metric is sampled `repeat` times (each sample is `inner` iterations) and
reported as min/median/max ms-per-call, so run-to-run timing noise is visible
rather than averaged away. Counts are fixed so numbers stay comparable across
runs and commits. Requires duho importable (PYTHONPATH=src, or installed).

Warm metrics (caches populated) are the ones CI regression-gates -- see
check_baseline.py. Cold metrics reproduce the real per-invocation cost and are
reported for insight, not gated (they are dominated by ast.parse noise).
"""
import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

# benchmarks/ is not a package; make the sibling _bench importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import duho  # noqa: E402
import _bench  # noqa: E402


def _print_table(title, metrics):
    print(title)
    print(f"{'metric':22s} {'median':>10s} {'min':>10s} {'max':>10s}   (ms/call)")
    for key, m in metrics.items():
        print(
            f"{key:22s} {m['median_ms']:10.4f} {m['min_ms']:10.4f} "
            f"{m['max_ms']:10.4f}"
        )


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run duho benchmarks")
    ap.add_argument(
        "--save", action="store_true", help="write result to benchmarks/results/"
    )
    ap.add_argument(
        "--name", default=None, help="result name (default duho-<ver>-py<ver>)"
    )
    ap.add_argument(
        "--cold", action="store_true", help="also run the cold (per-invocation) set"
    )
    ap.add_argument("--json", default=None, help="write the metrics JSON to PATH")
    args = ap.parse_args(argv)

    pyver = f"py{sys.version_info.major}{sys.version_info.minor}"
    name = args.name or f"duho-{duho.__version__}-{pyver}"

    warm = _bench.warm_metrics()
    cold = _bench.cold_metrics() if args.cold else {}
    metrics = {**warm, **cold}

    result = {
        "name": name,
        "duho_version": duho.__version__,
        "python": platform.python_version(),
        "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "iterations": {
            "build_inner": _bench.BUILD_INNER,
            "parse_inner": _bench.PARSE_INNER,
            "repeat": _bench.REPEAT,
        },
        "metrics": metrics,
    }

    print("=== Duho Benchmark ===")
    print(f"{name}  ({result['python']} on {result['processor']})")
    _print_table("\n-- warm (cached; gated) --", warm)
    if cold:
        _print_table("\n-- cold (per-invocation; informational) --", cold)

    if args.save:
        dest = Path(__file__).resolve().parent / "results"
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{name}.json"
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(f"\nsaved: {out}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(f"json: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
