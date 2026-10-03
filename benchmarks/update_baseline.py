#!/usr/bin/env python3
"""Regenerate the committed benchmark baseline for the CURRENT interpreter.

``benchmarks/baseline.json`` records the median of each gated metric, keyed by
Python ``major.minor``, and is what ``check_baseline.py`` compares a run against.
Because timings differ per interpreter version, each version carries its own
entry; this script measures on whatever Python runs it and merges (only) that
version's entry, leaving other versions untouched. Each entry also carries
``calibration_ms`` -- the median of a fixed, duho-independent workload (see
``_bench.calibration_metric``) that ``check_baseline.py`` uses to normalise
its thresholds for runner speed.

Run it ONLY after an intentional, understood performance change, on a quiet
machine of the SAME kind ``check_baseline.py`` will later compare against --
ideally from the CI benchmark job's own runner/artifacts, not a contributor
laptop (a local run is a sanity check, not baseline-grade evidence: see
benchmarks/README.md). See CONTRIBUTING.md.

    python benchmarks/update_baseline.py            # update current py entry
    python benchmarks/update_baseline.py -n 15       # more startup samples

Requires duho importable (PYTHONPATH=src, or installed).
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _bench  # noqa: E402
import bench_startup  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "baseline.json"


def build_entry(startup_samples=10):
    """Measure and return this interpreter's baseline entry."""
    calibration = _bench.calibration_metric()
    warm = _bench.warm_metrics()
    startup = bench_startup.measure(max(startup_samples, 10))
    return {
        # Runner-speed reference (Plan 40); see _bench.calibration_metric.
        "calibration_ms": calibration["median_ms"],
        "warm": {k: v["median_ms"] for k, v in warm.items()},
        "startup": startup["deltas"],
        "measured": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def load_baseline():
    if BASELINE.exists():
        return json.loads(BASELINE.read_text())
    return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Update the benchmark baseline")
    ap.add_argument("-n", type=int, default=10, help="startup samples (>=10)")
    args = ap.parse_args(argv)

    py_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    data = load_baseline()
    data[py_minor] = build_entry(args.n)
    BASELINE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"updated baseline for python {py_minor} -> {BASELINE}")
    print(json.dumps(data[py_minor], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
