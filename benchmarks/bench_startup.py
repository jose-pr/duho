#!/usr/bin/env python3
"""Fresh-process startup benchmarks for duho (the cost every CLI invocation pays).

The parser-build/parse benchmarks in run.py time work inside a warm process --
but a real CLI runs once and exits, so the dominant cost is interpreter startup
plus ``import duho`` plus one cold build+parse. This measures that, in a fresh
subprocess, as a min-of-N (N>=10, min not mean: the fastest run is the one least
perturbed by scheduler noise).

Absolute wall times depend on the machine, so the headline numbers are the
duho **deltas over bare python** -- ``import duho`` minus ``python -c pass``, and
end-to-end minus ``python -c pass``. The delta normalizes away runner speed, so
it is comparable across machines and is what check_baseline.py gates on.

    python benchmarks/bench_startup.py            # print summary
    python benchmarks/bench_startup.py --json PATH # also write JSON
    python benchmarks/bench_startup.py -n 20       # samples per measurement

Requires duho importable (PYTHONPATH=src, or installed).
"""
import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# End-to-end: import duho, build a 2-field parser, parse it -- one whole
# invocation's worth of duho work in a fresh process.
E2E_CODE = (
    "import duho\n"
    "class A(duho.Args):\n"
    "    name: str\n"
    "    ('--name',)\n"
    "    count: int = 1\n"
    "    ('--count',)\n"
    "duho.parse(A, ['--name', 'x'])\n"
)


def subprocess_ms(code, n, env):
    """Wall time of ``python -c code`` in a fresh process: (min, median) ms."""
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        subprocess.run(
            [sys.executable, "-c", code], check=True, env=env, capture_output=True
        )
        times.append((time.perf_counter() - t0) * 1000)
    return min(times), statistics.median(times)


def measure(n):
    # Give the child the same import path this process used, so an editable /
    # PYTHONPATH=src checkout is importable without installation.
    import os

    env = dict(os.environ)
    src = str(Path(__file__).resolve().parent.parent / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src + (os.pathsep + existing if existing else "")

    base_min, base_med = subprocess_ms("pass", n, env)
    argp_min, argp_med = subprocess_ms("import argparse", n, env)
    duho_min, duho_med = subprocess_ms("import duho", n, env)
    e2e_min, e2e_med = subprocess_ms(E2E_CODE, n, env)

    return {
        "abs": {
            "python_pass": {"min_ms": round(base_min, 2), "median_ms": round(base_med, 2)},
            "import_argparse": {"min_ms": round(argp_min, 2), "median_ms": round(argp_med, 2)},
            "import_duho": {"min_ms": round(duho_min, 2), "median_ms": round(duho_med, 2)},
            "e2e_build_parse": {"min_ms": round(e2e_min, 2), "median_ms": round(e2e_med, 2)},
        },
        # The gated deltas: duho's added cost over bare python (min-vs-min), which
        # normalizes out runner speed.
        "deltas": {
            "import_duho_delta": round(duho_min - base_min, 2),
            "e2e_delta": round(e2e_min - base_min, 2),
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="duho fresh-process startup benchmarks")
    ap.add_argument("-n", type=int, default=10, help="samples per measurement (>=10)")
    ap.add_argument("--json", default=None, help="write the metrics JSON to PATH")
    args = ap.parse_args(argv)

    m = measure(max(args.n, 10))
    a = m["abs"]
    d = m["deltas"]
    print("=== Duho startup (fresh process, min-of-%d) ===" % max(args.n, 10))
    print(f"{'measurement':22s} {'min':>9s} {'median':>9s}  (ms)")
    for key, label in (
        ("python_pass", "python -c pass"),
        ("import_argparse", "import argparse"),
        ("import_duho", "import duho"),
        ("e2e_build_parse", "import+build+parse"),
    ):
        print(f"{label:22s} {a[key]['min_ms']:9.2f} {a[key]['median_ms']:9.2f}")
    print()
    print(f"duho tax: import duho over bare python : {d['import_duho_delta']:7.2f} ms")
    print(f"duho tax: end-to-end over bare python  : {d['e2e_delta']:7.2f} ms")

    if args.json:
        result = {
            "python": platform.python_version(),
            "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "samples": max(args.n, 10),
            **m,
        }
        Path(args.json).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(f"\njson: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
