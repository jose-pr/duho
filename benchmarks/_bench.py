#!/usr/bin/env python3
"""Shared benchmark core for duho.

Home of the sample workloads (arg classes, subcommand trees, the field-type
matrix) and the in-process **warm** metric functions, so ``run.py``,
``update_baseline.py`` and ``check_baseline.py`` all measure the exact same
things the same way. Kept dependency-free (stdlib + duho) and out of the sdist
(``benchmarks/`` is excluded), so it never ships to users.

A "warm" metric is a build/parse whose duho caches (`_duho_constants_`,
`_duho_clsargs_`, `_duho_builders_`, the AST `lru_cache`) are already populated
-- what a long-lived process or a repeated call pays. A "cold" metric drops
every cache first, reproducing what a fresh CLI *invocation* pays. Only warm
metrics are stable enough to gate CI on; cold numbers are reported for insight.
"""
import enum
import statistics
import timeit
import typing as ty

import duho
from duho import Args, Cli, Cmd
from duho import _introspect


# ---------------------------------------------------------------------------
# Sample workloads
# ---------------------------------------------------------------------------


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


class _Color(enum.Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class ListArgs(Args):
    """A list[T] collection field (extend action, element conversion)."""

    items: ty.List[int] = []
    ("--items",)


class SetArgs(Args):
    """A set[T] collection field (custom _CollectionAction)."""

    tags: ty.Set[str] = set()
    ("--tags",)


class TupleArgs(Args):
    """A tuple[T, ...] collection field (custom _CollectionAction)."""

    coords: ty.Tuple[int, ...] = ()
    ("--coords",)


class UnionArgs(Args):
    """A multi-member Union field (composed factory ladder)."""

    value: ty.Union[int, float, str] = 0
    ("--value",)


class EnumArgs(Args):
    """An Enum field (name factory + choices metavar)."""

    color: _Color = _Color.RED
    ("--color",)


class LiteralArgs(Args):
    """A Literal[...] field (choices + round-trip factory)."""

    mode: ty.Literal["fast", "slow", "auto"] = "fast"
    ("--mode",)


#: The field-type matrix: builder cost differs substantially per branch of the
#: `_argbuilder_`/`_factory_for` ladder (collection/union/enum/Literal).
FIELD_MATRIX = {
    "field.list": ListArgs,
    "field.set": SetArgs,
    "field.tuple": TupleArgs,
    "field.union": UnionArgs,
    "field.enum": EnumArgs,
    "field.literal": LiteralArgs,
}

CACHE_ATTRS = ("_duho_constants_", "_duho_clsargs_", "_duho_builders_")


def make_tree(n: int) -> "type":
    """Build a fresh ``Cli`` root with ``n`` dynamically-created subcommands.

    Subcommands are ``type(...)``-created (as ``duho.command``/discovery would
    produce) so the tree exercises the dynamic-class path (no literal ClassDef),
    which is exactly where the P5 getsource guard matters. A fresh root/subs each
    call keeps cold builds honest (nothing is pre-cached).
    """
    subs = []
    for i in range(n):
        ns = {
            "__doc__": "Subcommand %d." % i,
            "__annotations__": {"alpha": str, "beta": int, "gamma": bool},
            "alpha": "a",
            "beta": i,
            "gamma": False,
            "__module__": __name__,
        }
        subs.append(type("Sub%d_%d" % (n, i), (Cmd,), ns))
    root = type(
        "Root%d" % n,
        (Cli,),
        {"__doc__": "Root with %d subs." % n, "__module__": __name__},
    )
    root._subcommands_ = subs
    return root


def drop_caches(cls) -> None:
    """Evict every duho cache reachable from ``cls`` (and its subcommand tree),
    reproducing a cold build."""
    seen = set()

    def _drop(klass):
        if klass in seen:
            return
        seen.add(klass)
        for base in klass.__mro__:
            for attr in CACHE_ATTRS:
                if attr in vars(base):
                    delattr(base, attr)
        for sub in getattr(klass, "_subcommands_", None) or ():
            _drop(sub)

    _drop(cls)
    _introspect._module_index.cache_clear()


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

BUILD_INNER = 200
PARSE_INNER = 2000
COLD_INNER = 5
REPEAT = 5


def sample(fn, inner, repeat=REPEAT, warmup=True):
    """Return ms-per-call as min/median/max over ``repeat`` samples."""
    if warmup:
        fn()
    per_call = [timeit.timeit(fn, number=inner) / inner * 1000 for _ in range(repeat)]
    return {
        "median_ms": round(statistics.median(per_call), 4),
        "min_ms": round(min(per_call), 4),
        "max_ms": round(max(per_call), 4),
    }


def warm_metrics() -> "dict":
    """The gated, in-process WARM metrics (caches already populated).

    These are the numbers ``check_baseline.py`` regression-gates: they are
    deterministic (no subprocess, no import-cache effects) and reflect the
    steady-state cost of building and parsing a parser whose declarations are
    cached.
    """
    simple = duho.parser(SimpleArgs)
    complex_ = duho.parser(ComplexArgs)
    metrics = {
        "build.simple": sample(lambda: duho.parser(SimpleArgs), BUILD_INNER),
        "build.complex": sample(lambda: duho.parser(ComplexArgs), BUILD_INNER),
        "parse.simple": sample(
            lambda: simple.parse_args(["--name", "test", "--count", "5"]),
            PARSE_INNER,
        ),
        "parse.complex": sample(
            lambda: complex_.parse_args(
                [
                    "--name", "app", "--version", "2.0.0", "--output", "out.txt",
                    "--verbose", "--config", "app.yml", "--workers", "8",
                ]
            ),
            PARSE_INNER,
        ),
    }

    # Tree scaling (warm): build + parse for N in {1, 10, 50} subcommands.
    for n in (1, 10, 50):
        root = make_tree(n)
        duho.parser(root)  # warm
        metrics["tree.build.%d" % n] = sample(lambda r=root: duho.parser(r), 50)
        parser = duho.parser(root)
        argv = ["Sub%d_0" % n, "--alpha", "x", "--beta", "9"]
        metrics["tree.parse.%d" % n] = sample(
            lambda p=parser, a=argv: p.parse_args(a), PARSE_INNER
        )

    # Field-type matrix (warm builder cost per ladder branch).
    for key, cls in FIELD_MATRIX.items():
        duho.parser(cls)  # warm
        metrics["build.%s" % key.split(".", 1)[1]] = sample(
            lambda c=cls: duho.parser(c), BUILD_INNER
        )

    return metrics


def cold_metrics() -> "dict":
    """Informational COLD metrics (caches dropped before each build).

    Reproduces the real per-invocation cost a fresh CLI process pays. Noisier
    than warm metrics (dominated by ``ast.parse`` of the user's own file), so
    reported but NOT gated.
    """
    metrics = {}

    def cold_build(cls):
        drop_caches(cls)
        duho.parser(cls)

    metrics["cold.build.complex"] = sample(
        lambda: cold_build(ComplexArgs), COLD_INNER
    )
    for n in (10, 50):
        root = make_tree(n)

        def _cold(r=root):
            drop_caches(r)
            duho.parser(r)

        metrics["cold.tree.%d" % n] = sample(_cold, COLD_INNER)
    return metrics
