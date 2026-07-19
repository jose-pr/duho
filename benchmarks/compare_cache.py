#!/usr/bin/env python3
"""A/B benchmark: parser construction COLD (per-invocation) vs WARM (cached).

duho caches the module AST index and the per-class argument declarations
(`_duho_constants_`, `_duho_clsargs_`, `_duho_builders_`). Those caches persist
for the life of a process but die with it -- so the COLD path (caches dropped,
class source re-read and `ast.parse`-d for every class in the MRO) is exactly
what a real, run-once CLI *invocation* pays, while the WARM path is what a
long-lived process or a repeated in-process build pays.

This script measures both paths in the SAME process on the SAME machine, so the
two numbers are directly comparable -- unlike comparing a historical local run
against a current one. The COLD number is the one that matters for CLI startup;
the ratio shows how much the caches save a warm caller.

    python benchmarks/compare_cache.py
"""
import statistics
import sys
import timeit
import typing as ty

import duho
from duho import Args
from duho import _introspect

# The prototype's cost was dominated by re-parsing; keep the uncached loop small.
UNCACHED_INNER = 20
CACHED_INNER = 200
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


CACHE_ATTRS = ("_duho_constants_", "_duho_clsargs_", "_duho_builders_")


def _drop_caches(cls):
    """Evict every duho cache reachable from cls, mimicking a cold build."""
    for klass in cls.__mro__:
        for attr in CACHE_ATTRS:
            if attr in vars(klass):
                delattr(klass, attr)
    _introspect._module_index.cache_clear()


def sample(fn, inner, repeat=REPEAT):
    per_call = [timeit.timeit(fn, number=inner) / inner * 1000 for _ in range(repeat)]
    return {
        "median_ms": round(statistics.median(per_call), 4),
        "min_ms": round(min(per_call), 4),
        "max_ms": round(max(per_call), 4),
    }


def build_uncached(cls):
    _drop_caches(cls)
    duho.parser(cls)


def main():
    print("=== duho: parser build, COLD (per-invocation) vs WARM (cached) ===")
    print(f"python {sys.version.split()[0]}")
    print(f"{'case':22s} {'median':>10s} {'min':>10s} {'max':>10s}   (ms/call)")

    results = {}
    for label, cls in (("simple", SimpleArgs), ("complex", ComplexArgs)):
        un = sample(lambda c=cls: build_uncached(c), UNCACHED_INNER)
        duho.parser(cls)  # warm
        ca = sample(lambda c=cls: duho.parser(c), CACHED_INNER)
        results[label] = (un, ca)

        print(f"{label + ' cold':22s} {un['median_ms']:10.4f} "
              f"{un['min_ms']:10.4f} {un['max_ms']:10.4f}")
        print(f"{label + ' warm':22s} {ca['median_ms']:10.4f} "
              f"{ca['min_ms']:10.4f} {ca['max_ms']:10.4f}")

    print()
    for label, (un, ca) in results.items():
        if ca["median_ms"]:
            print(f"{label}: warm is {un['median_ms'] / ca['median_ms']:.0f}x the "
                  f"cold build ({un['median_ms']:.2f} ms cold -> {ca['median_ms']:.3f} "
                  f"ms warm, median)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
