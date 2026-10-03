#!/usr/bin/env python3
"""CI regression gate: compare a fresh run against the committed baseline.

Measures this interpreter's gated metrics and compares them to
``benchmarks/baseline.json`` for the matching Python ``major.minor``:

  * **warm metrics** (build/parse/tree/field-matrix, from ``_bench``): fail if a
    median exceeds its baseline by more than ``WARM_THRESHOLD`` (1.5x);
  * **startup deltas** (duho's added cost over bare python, from
    ``bench_startup``): fail if a delta exceeds its baseline by more than
    ``STARTUP_THRESHOLD`` (1.3x). The delta cancels the fixed per-process
    overhead shared by both sides of the subtraction, not the machine's clock
    speed -- it is meaningful here only because a baseline and its comparison
    run are both produced on the same CI runner image (see bench_startup.py).

Both groups are **normalised for runner speed** before being
compared to their threshold -- but each against a calibration reference from
its OWN measurement domain, not a single shared one:

  * **warm** metrics are in-process, CPU-bound work, so they are normalised
    against ``_bench.calibration_metric`` -- building and parsing a plain
    ``argparse`` parser in-process, the same way.
  * **startup** deltas are fresh-process subprocess spawns, so they are
    normalised against ``python -c pass``'s own median (``bench_startup.py``
    already measures this as ``abs.python_pass``) -- a bare subprocess spawn,
    the same way.

A single shared calibration ratio was tried first and rejected: measured
directly on a real confirming CI run, a runner-speed swing moved the
in-process workload's ratio by a different amount than the subprocess
spawn's ratio (python_pass 13-17% faster vs. the in-process calibration 39%
faster, same run) -- dividing ``import_duho_delta`` by the in-process ratio
turned its own harmless 0.87x raw ratio into a false "1.39x REGRESSION".
Domain-matching each group to its own reference is what makes the
cancellation in the next paragraph actually hold.

Each group's calibration ratio is current/baseline for that reference. A
uniformly slower (or faster) shared ``ubuntu-latest`` runner moves a group's
calibration ratio by the same factor it moves every metric IN THAT GROUP, so
dividing cancels that common factor; a regression confined to duho's own code
still moves a metric's ratio without moving its group's calibration ratio, so
it still trips the gate. A baseline entry from before this change has no
``calibration_ms``/``calibration_subprocess_ms``, so the corresponding ratio
falls back to 1.0 (unnormalised, the old behavior) until the entry is
regenerated.

Thresholds are deliberately generous -- CI runner timing noise is real -- so a
trip means a structural regression, not jitter. When the baseline has no entry
for the running Python version, the check is SKIPPED (exit 0) with a note, so a
version without a committed baseline never spuriously fails; add one with
``update_baseline.py`` run on the SAME kind of machine that will be compared
against it (ideally: from the CI benchmark job's own artifacts, not a
contributor's laptop -- see benchmarks/README.md).

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


def _check_group(current, baseline, threshold, calibration_ratio, floor=0.0):
    """Return a list of (metric, baseline, current, ratio) regressions.

    ``ratio`` is each metric's raw ``current / baseline`` divided by
    ``calibration_ratio`` -- see the module docstring and
    ``_bench.calibration_metric``.
    """
    regressions = []
    for name, base in baseline.items():
        cur = current.get(name)
        if cur is None:
            continue
        if base <= 0 or (floor and base < floor):
            continue
        ratio = (cur / base) / calibration_ratio
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

    # Runner-speed references: see the module docstring for why
    # there are two, domain-matched ones rather than one shared ratio. A
    # baseline entry predating this change has neither key -- fall back to
    # an unnormalised 1.0 ratio (the old behavior) rather than failing.
    def _ratio(current, baseline_value):
        return current / baseline_value if baseline_value else 1.0

    calibration_current = _bench.calibration_metric()["median_ms"]
    calibration_baseline = entry.get("calibration_ms")
    calibration_ratio = _ratio(calibration_current, calibration_baseline)

    warm_current = {k: v["median_ms"] for k, v in _bench.warm_metrics().items()}
    startup_measured = bench_startup.measure(max(args.n, 10))
    startup_current = startup_measured["deltas"]
    subprocess_current = startup_measured["abs"]["python_pass"]["median_ms"]
    subprocess_baseline = entry.get("calibration_subprocess_ms")
    subprocess_ratio = _ratio(subprocess_current, subprocess_baseline)

    warm_reg = _check_group(
        warm_current, entry.get("warm", {}), WARM_THRESHOLD, calibration_ratio
    )
    startup_reg = _check_group(
        startup_current,
        entry.get("startup", {}),
        STARTUP_THRESHOLD,
        subprocess_ratio,
        STARTUP_FLOOR_MS,
    )

    print(f"=== regression gate (python {py_minor}) ===")

    def _calibration_line(label, current, baseline_value, ratio):
        base_str = f"{baseline_value:.4f}" if baseline_value else "n/a"
        note = "" if baseline_value else "  (no baseline value; unnormalised)"
        print(f"{label}: base {base_str:>8s}  cur {current:8.4f}  {ratio:5.2f}x{note}")

    _calibration_line(
        "calibration, in-process (plain-argparse build+parse, warm reference)",
        calibration_current,
        calibration_baseline,
        calibration_ratio,
    )
    _calibration_line(
        "calibration, subprocess (python -c pass, startup reference)",
        subprocess_current,
        subprocess_baseline,
        subprocess_ratio,
    )
    print(
        f"warm metrics <= {WARM_THRESHOLD}x baseline median (calibration-normalised):"
    )
    for name, base in sorted(entry.get("warm", {}).items()):
        cur = warm_current.get(name)
        flag = ""
        if cur is not None and base > 0:
            ratio = (cur / base) / calibration_ratio
            flag = "  <-- REGRESSION" if ratio > WARM_THRESHOLD else ""
            print(f"  {name:22s} base {base:8.4f}  cur {cur:8.4f}  {ratio:5.2f}x{flag}")
    print(
        f"startup deltas <= {STARTUP_THRESHOLD}x baseline (floor {STARTUP_FLOOR_MS} ms, "
        "calibration-normalised):"
    )
    for name, base in sorted(entry.get("startup", {}).items()):
        cur = startup_current.get(name)
        if cur is None:
            continue
        raw_ratio = cur / base if base > 0 else float("nan")
        ratio = raw_ratio / subprocess_ratio if base > 0 else raw_ratio
        note = " (below floor; not gated)" if base < STARTUP_FLOOR_MS else ""
        flag = "  <-- REGRESSION" if (name, base, cur, ratio) in startup_reg else ""
        print(
            f"  {name:22s} base {base:8.2f}  cur {cur:8.2f}  {ratio:5.2f}x{note}{flag}"
        )

    regressions = warm_reg + startup_reg
    if regressions:
        print(f"\nFAIL: {len(regressions)} metric(s) regressed beyond threshold.")
        return 1
    print("\nOK: all gated metrics within threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
