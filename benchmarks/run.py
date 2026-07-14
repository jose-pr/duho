#!/usr/bin/env python3
"""Structured benchmark runner for duho.

Produces a comparable JSON result plus a human summary. Save a run to the
history with --save; results land in benchmarks/results/<name>.json where
<name> defaults to duho-<version>-py<major><minor>.

    python benchmarks/run.py            # print summary only
    python benchmarks/run.py --save     # also write benchmarks/results/<name>.json
    python benchmarks/run.py --name foo # custom result name

Each metric is sampled `repeat` times (each sample is `inner` iterations) and
reported as min/median/max ms-per-call, so run-to-run timing noise is visible
rather than averaged away. Counts are fixed so numbers stay comparable across
runs and commits. Requires duho importable (PYTHONPATH=src, or installed).
"""
import argparse
import json
import platform
import statistics
import sys
import timeit
import typing as ty
from datetime import datetime, timezone
from pathlib import Path

import duho
from duho import Args

BUILD_INNER = 200
PARSE_INNER = 2000
REPEAT = 5


class SimpleArgs(Args):
    """Simple argument set."""
    name: str
    ("--name",)
    count: int = 1
    ("--count",)


class ComplexArgs(Args):
    """Complex argument set with many fields."""
    name: str
    ("--name",)
    version: str = "1.0.0"
    ("--version",)
    output: str = "output.txt"
    ("--output",)
    verbose: bool = False
    ("--verbose",)
    dry_run: bool = False
    ("--dry-run",)
    config: ty.Optional[str] = None
    ("--config",)
    workers: int = 4
    ("--workers",)


def sample(fn, inner, repeat=REPEAT):
    """Return ms-per-call as min/median/max over `repeat` samples."""
    fn()  # warmup
    per_call = [timeit.timeit(fn, number=inner) / inner * 1000 for _ in range(repeat)]
    return {
        "median_ms": round(statistics.median(per_call), 4),
        "min_ms": round(min(per_call), 4),
        "max_ms": round(max(per_call), 4),
    }


def measure():
    simple = duho.parser(SimpleArgs)
    complex_ = duho.parser(ComplexArgs)
    return {
        "build.simple": sample(lambda: duho.parser(SimpleArgs), BUILD_INNER),
        "build.complex": sample(lambda: duho.parser(ComplexArgs), BUILD_INNER),
        "parse.simple": sample(
            lambda: simple.parse_args(["--name", "test", "--count", "5"]), PARSE_INNER
        ),
        "parse.complex": sample(
            lambda: complex_.parse_args(
                ["--name", "app", "--version", "2.0.0", "--output", "out.txt",
                 "--verbose", "--config", "app.yml", "--workers", "8"]
            ),
            PARSE_INNER,
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run duho benchmarks")
    ap.add_argument("--save", action="store_true", help="write result to benchmarks/results/")
    ap.add_argument("--name", default=None, help="result name (default duho-<ver>-py<ver>)")
    args = ap.parse_args(argv)

    pyver = f"py{sys.version_info.major}{sys.version_info.minor}"
    name = args.name or f"duho-{duho.__version__}-{pyver}"
    metrics = measure()
    result = {
        "name": name,
        "duho_version": duho.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "iterations": {"build_inner": BUILD_INNER, "parse_inner": PARSE_INNER, "repeat": REPEAT},
        "metrics": metrics,
    }

    print("=== Duho Benchmark ===")
    print(f"{name}  ({result['python']} on {result['processor']})")
    print(f"{'metric':16s} {'median':>10s} {'min':>10s} {'max':>10s}   (ms/call)")
    for key, m in metrics.items():
        print(f"{key:16s} {m['median_ms']:10.4f} {m['min_ms']:10.4f} {m['max_ms']:10.4f}")

    if args.save:
        dest = Path(__file__).resolve().parent / "results"
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{name}.json"
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
