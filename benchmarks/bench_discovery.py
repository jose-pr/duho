#!/usr/bin/env python3
"""Discovery benchmark: cost of scanning + dispatching a directory of commands.

``discover_commands(path)`` imports every candidate command module to collect
its commands -- so its cost grows with the number of files AND with whatever
import-time work each file does. This generates a throwaway directory of N=25
trivial command files and measures:

  * ``discover_commands(dir)`` -- the full discovery scan (min-of-N, fresh dir
    each sample so the module import cache never hides the real cost);
  * ``app(source=dir, argv=[one command])`` -- discover + build + parse +
    dispatch one command end-to-end.

This is the structurally dominant cost for app-style CLIs at scale, and the
workload the P7 lazy-discovery design targets. Reported informationally (import
cost is inherently one-shot / cache-sensitive, so it is not part of the CI
regression gate).

    python benchmarks/bench_discovery.py
    python benchmarks/bench_discovery.py -n 5 --files 25

Requires duho importable (PYTHONPATH=src, or installed).
"""
import argparse
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

import duho

# Each generated file is a module command: a top-level ``main`` (the app-style
# discovery pattern) plus a ``register`` hook that adds one option, so dispatch
# exercises build+parse of a real argument. Deliberately NO ``Cmd`` subclass, so
# each file contributes exactly one command (named by its file stem).
_COMMAND_TEMPLATE = '''\
"""Command number {i}: {name}."""


def register(parser, args):
    parser.add_argument("--value", type=int, default={i})


def main(args=None):
    return 0
'''


def _make_command_dir(n_files):
    d = Path(tempfile.mkdtemp(prefix="duho_bench_disc_"))
    for i in range(n_files):
        name = "cmd%02d" % i
        (d / (name + ".py")).write_text(
            _COMMAND_TEMPLATE.format(i=i, name=name, cls="Cmd%02d" % i)
        )
    return d


def _timed(fn):
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


def measure(n_files, samples):
    discover_times = []
    dispatch_times = []
    for _ in range(samples):
        d = _make_command_dir(n_files)
        try:
            discover_times.append(_timed(lambda: duho.discover_commands(d)))
        finally:
            shutil.rmtree(d, ignore_errors=True)

        d = _make_command_dir(n_files)
        try:
            dispatch_times.append(
                _timed(lambda: duho.app(source=d, argv=["cmd00", "--value", "7"]))
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    return {
        "discover_%d.min_ms" % n_files: round(min(discover_times), 3),
        "discover_%d.median_ms" % n_files: round(statistics.median(discover_times), 3),
        "dispatch_1.min_ms": round(min(dispatch_times), 3),
        "dispatch_1.median_ms": round(statistics.median(dispatch_times), 3),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="duho discovery benchmark")
    ap.add_argument("--files", type=int, default=25, help="command files to generate")
    ap.add_argument("-n", type=int, default=5, help="samples per measurement")
    args = ap.parse_args(argv)

    m = measure(args.files, args.n)
    print("=== Duho discovery (%d command files, min-of-%d) ===" % (args.files, args.n))
    print(
        "discover_commands(dir): min %.3f ms  median %.3f ms"
        % (m["discover_%d.min_ms" % args.files], m["discover_%d.median_ms" % args.files])
    )
    print(
        "app() discover+dispatch 1 cmd: min %.3f ms  median %.3f ms"
        % (m["dispatch_1.min_ms"], m["dispatch_1.median_ms"])
    )
    print(
        "\nper-file discovery cost ~ %.3f ms/file"
        % (m["discover_%d.min_ms" % args.files] / args.files)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
