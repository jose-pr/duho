#!/usr/bin/env python3
"""CI regression gate: compare a fresh run against the committed baseline.

Measures this interpreter's gated metrics and compares them to
``benchmarks/baseline.json`` for the matching Python ``major.minor``:

  * **warm metrics** (build/parse/tree/field-matrix, from ``_bench``): fail if a
    median exceeds its baseline by more than ``WARM_THRESHOLD`` (1.5x);
  * **startup deltas** (duho's added cost over bare python, from
    ``bench_startup``): fail if a delta exceeds its baseline by more than
    ``STARTUP_THRESHOLD`` (1.3x). The delta normalizes out runner speed, so a
    tighter bound is safe.

Thresholds are deliberately generous -- CI runner timing noise is real -- so a
trip means a structural regression, not jitter. When the baseline has no entry
for the running Python version, the check is SKIPPED (exit 0) with a note, so a
version without a committed baseline never spuriously fails; add one with
``update_baseline.py``.

    python benchmarks/check_baseline.py
    python benchmarks/check_baseline.py -n 15    # startup samples

Exit code: 0 = within thresholds (or skipped), 1 = regression detected.
Requires duho importable (PYTHONPATH=src, or installed).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _bench  # noqa: E402
import bench_startup  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "baseline.json"

WARM_THRESHOLD = 1.5
STARTUP_THRESHOLD = 1.3
#: Deltas below this many ms are dominated by subprocess-spawn jitter; comparing
#: their ratio is meaningless, so they are reported but never fail the gate.
STARTUP_FLOOR_MS = 5.0


def _check_group(current, baseline, threshold, floor=0.0):
    """Return a list of (metric, baseline, current, ratio) regressions."""
    regressions = []
    for name, base in baseline.items():
        cur = current.get(name)
        if cur is None:
            continue
        if base <= 0 or (floor and base < floor):
            continue
        ratio = cur / base
        if ratio > threshold:
            regressions.append((name, base, cur, ratio))
    return regressions


def main(argv=None):
    ap = argparse.ArgumentParser(description="Benchmark regression gate")
    ap.add_argument("-n", type=int, default=10, help="startup samples (>=10)")
    args = ap.parse_args(argv)

    py_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    if not BASELINE.exists():
        print("no baseline.json committed; skipping regression gate")
        return 0
    data = json.loads(BASELINE.read_text())
    entry = data.get(py_minor)
    if entry is None:
        print(
            f"no baseline entry for python {py_minor} "
            f"(have: {', '.join(sorted(data)) or 'none'}); skipping gate. "
            f"Add one with benchmarks/update_baseline.py."
        )
        return 0

    warm_current = {k: v["median_ms"] for k, v in _bench.warm_metrics().items()}
    startup_current = bench_startup.measure(max(args.n, 10))["deltas"]

    warm_reg = _check_group(warm_current, entry.get("warm", {}), WARM_THRESHOLD)
    startup_reg = _check_group(
        startup_current, entry.get("startup", {}), STARTUP_THRESHOLD, STARTUP_FLOOR_MS
    )

    print(f"=== regression gate (python {py_minor}) ===")
    print(f"warm metrics <= {WARM_THRESHOLD}x baseline median:")
    for name, base in sorted(entry.get("warm", {}).items()):
        cur = warm_current.get(name)
        flag = ""
        if cur is not None and base > 0:
            ratio = cur / base
            flag = "  <-- REGRESSION" if ratio > WARM_THRESHOLD else ""
            print(f"  {name:22s} base {base:8.4f}  cur {cur:8.4f}  {ratio:5.2f}x{flag}")
    print(f"startup deltas <= {STARTUP_THRESHOLD}x baseline (floor {STARTUP_FLOOR_MS} ms):")
    for name, base in sorted(entry.get("startup", {}).items()):
        cur = startup_current.get(name)
        if cur is None:
            continue
        ratio = cur / base if base > 0 else float("nan")
        note = " (below floor; not gated)" if base < STARTUP_FLOOR_MS else ""
        flag = "  <-- REGRESSION" if (name, base, cur, ratio) in startup_reg else ""
        print(f"  {name:22s} base {base:8.2f}  cur {cur:8.2f}  {ratio:5.2f}x{note}{flag}")

    regressions = warm_reg + startup_reg
    if regressions:
        print(f"\nFAIL: {len(regressions)} metric(s) regressed beyond threshold.")
        return 1
    print("\nOK: all gated metrics within threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
