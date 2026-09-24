#!/usr/bin/env python3
"""Fresh-process startup benchmarks for duho (the cost every CLI invocation pays).

The parser-build/parse benchmarks in run.py time work inside a warm process --
but a real CLI runs once and exits, so the dominant cost is interpreter startup
plus ``import duho`` plus one cold build+parse. This measures that, in a fresh
subprocess, as a min-of-N (N>=10, min not mean: the fastest run is the one least
perturbed by scheduler noise).

The headline numbers are the duho **deltas over bare python** -- ``import duho``
minus ``python -c pass``, and end-to-end minus ``python -c pass`` (min-vs-min).
The delta cancels the fixed per-process overhead common to both sides (spawn +
interpreter bootstrap), so it isolates duho's own added CPU work -- but that
work still scales with the machine's clock speed like any other timing, so a
delta measured on one machine is only informally comparable to one measured on
another (two otherwise-identical processes measured 3-4x apart across a native
Windows run and a WSL run on the same hardware). It is what check_baseline.py
gates on, comparing this run against the committed baseline for the SAME
Python version -- CI runs both on the same runner image, which is what makes
the comparison meaningful, not the delta itself.

``e2e_build_parse`` runs the end-to-end snippet from a real ``.py`` file (not
``python -c``), because ``-c`` source has no ``__file__`` -- duho's class-body
introspection (``inspect.getsource``) then raises OSError and silently skips
the AST scan, so a ``-c``-based e2e number never exercises the per-invocation
AST/getsource path it claims to measure. ``e2e_no_source`` keeps the old
``-c``-based variant as a separate, informational (not gated) metric, for
comparison.

    python benchmarks/bench_startup.py            # print summary
    python benchmarks/bench_startup.py --json PATH # also write JSON
    python benchmarks/bench_startup.py --save      # write benchmarks/results/<name>.json
    python benchmarks/bench_startup.py -n 20       # samples per measurement

Requires duho importable (PYTHONPATH=src, or installed).
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# benchmarks/ is not a package; make the sibling _bench importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import duho  # noqa: E402
import _bench  # noqa: E402

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


def _run_ms(args, n, env):
    """Wall time of ``args`` (a subprocess argv) in a fresh process, run ``n``
    times: (min, median, max) ms."""
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        subprocess.run(args, check=True, env=env, capture_output=True)
        times.append((time.perf_counter() - t0) * 1000)
    return min(times), statistics.median(times), max(times)


def subprocess_ms(code, n, env):
    """Wall time of ``python -c code`` in a fresh process: (min, median, max) ms."""
    return _run_ms([sys.executable, "-c", code], n, env)


def source_ms(path, n, env):
    """Wall time of ``python path`` (a real .py file) in a fresh process:
    (min, median, max) ms."""
    return _run_ms([sys.executable, str(path)], n, env)


def _stats(min_ms, med_ms, max_ms):
    return {
        "min_ms": round(min_ms, 2),
        "median_ms": round(med_ms, 2),
        "max_ms": round(max_ms, 2),
    }


def measure(n):
    # Give the child the same import path this process used, so an editable /
    # PYTHONPATH=src checkout is importable without installation.
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parent.parent / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src + (os.pathsep + existing if existing else "")

    base = subprocess_ms("pass", n, env)
    argp = subprocess_ms("import argparse", n, env)
    duho_ = subprocess_ms("import duho", n, env)
    e2e_no_source = subprocess_ms(E2E_CODE, n, env)

    tmpdir = tempfile.mkdtemp(prefix="duho_bench_startup_")
    try:
        e2e_path = Path(tmpdir) / "e2e_probe.py"
        e2e_path.write_text(E2E_CODE)
        e2e = source_ms(e2e_path, n, env)
    finally:
        try:
            e2e_path.unlink()
            os.rmdir(tmpdir)
        except OSError:
            pass

    return {
        "abs": {
            "python_pass": _stats(*base),
            "import_argparse": _stats(*argp),
            "import_duho": _stats(*duho_),
            "e2e_build_parse": _stats(*e2e),
            "e2e_no_source": _stats(*e2e_no_source),
        },
        # The gated deltas: duho's added cost over bare python (min-vs-min).
        # See the module docstring for what this delta does and does not
        # normalize.
        "deltas": {
            "import_duho_delta": round(duho_[0] - base[0], 2),
            "e2e_delta": round(e2e[0] - base[0], 2),
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="duho fresh-process startup benchmarks")
    ap.add_argument("-n", type=int, default=10, help="samples per measurement (>=10)")
    ap.add_argument("--json", default=None, help="write the metrics JSON to PATH")
    ap.add_argument(
        "--save", action="store_true", help="write result to benchmarks/results/"
    )
    ap.add_argument(
        "--name", default=None, help="result name (default startup-<ver>-py<ver>)"
    )
    args = ap.parse_args(argv)

    n = max(args.n, 10)
    m = measure(n)
    a = m["abs"]
    d = m["deltas"]
    print("=== Duho startup (fresh process, min-of-%d) ===" % n)
    print(f"{'measurement':22s} {'min':>9s} {'median':>9s}  (ms)")
    for key, label in (
        ("python_pass", "python -c pass"),
        ("import_argparse", "import argparse"),
        ("import_duho", "import duho"),
        ("e2e_build_parse", "import+build+parse"),
        ("e2e_no_source", "  (same, via -c; informational)"),
    ):
        print(f"{label:22s} {a[key]['min_ms']:9.2f} {a[key]['median_ms']:9.2f}")
    print()
    print(f"duho tax: import duho over bare python : {d['import_duho_delta']:7.2f} ms")
    print(f"duho tax: end-to-end over bare python  : {d['e2e_delta']:7.2f} ms")

    pyver = "py%d%d" % (sys.version_info.major, sys.version_info.minor)
    name = args.name or f"startup-{duho.__version__}-{pyver}"
    metrics = {"startup.%s" % k: v for k, v in a.items()}
    metrics.update({"startup.%s" % k: v for k, v in d.items()})
    extra = {"duho_version": duho.__version__, "samples": n}

    if args.save:
        out = _bench.save_result(_bench.RESULTS_DIR, name, metrics, **extra)
        print(f"\nsaved: {out}")
    if args.json:
        result = _bench.result_envelope(name, metrics, **extra)
        Path(args.json).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(f"json: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
