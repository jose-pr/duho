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

Shares its sample workloads, cache-dropping and sampler with ``_bench.py`` (the
one place that knows about duho's internal cache attribute names and the
Args/Cmd/Cli framework-seed exception -- see ``_bench.drop_caches``), so a fix
to either only has to happen once.

    python benchmarks/compare_cache.py
"""

import sys
from pathlib import Path

# benchmarks/ is not a package; make the sibling _bench importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import duho  # noqa: E402
from _bench import ComplexArgs, SimpleArgs, drop_caches, sample  # noqa: E402

# The prototype's cost was dominated by re-parsing; keep the uncached loop small.
UNCACHED_INNER = 20
CACHED_INNER = 200
REPEAT = 5


def build_uncached(cls):
    drop_caches(cls)
    duho.parser(cls)


def main():
    print("=== duho: parser build, COLD (per-invocation) vs WARM (cached) ===")
    print(f"python {sys.version.split()[0]}")
    print(f"{'case':22s} {'median':>10s} {'min':>10s} {'max':>10s}   (ms/call)")

    results = {}
    for label, cls in (("simple", SimpleArgs), ("complex", ComplexArgs)):
        un = sample(lambda c=cls: build_uncached(c), UNCACHED_INNER, repeat=REPEAT)
        duho.parser(cls)  # warm
        ca = sample(lambda c=cls: duho.parser(c), CACHED_INNER, repeat=REPEAT)
        results[label] = (un, ca)

        print(
            f"{label + ' cold':22s} {un['median_ms']:10.4f} "
            f"{un['min_ms']:10.4f} {un['max_ms']:10.4f}"
        )
        print(
            f"{label + ' warm':22s} {ca['median_ms']:10.4f} "
            f"{ca['min_ms']:10.4f} {ca['max_ms']:10.4f}"
        )

    print()
    for label, (un, ca) in results.items():
        if ca["median_ms"]:
            print(
                f"{label}: warm is {un['median_ms'] / ca['median_ms']:.0f}x faster "
                f"than cold ({un['median_ms']:.2f} ms cold -> {ca['median_ms']:.3f} "
                f"ms warm, median)"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
