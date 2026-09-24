"""Version compatibility shims for duho.

Centralizes all version-specific logic and fallbacks.
"""

import logging as _logging
import types as _types
import typing as _ty

# Union type origins: Union on all versions, UnionType only 3.10+
UNION_ORIGINS: tuple = (
    _ty.Union,
    *([_types.UnionType] if hasattr(_types, "UnionType") else []),
)

#: The one true set of truthy/falsy text tokens (A074/C046/D052): every
#: bool-ish text parser in duho (the layered CLI/env/config converter, the
#: strict CLI text factory, ``Env.bool``, ``logging.traceback_enabled``)
#: matches against these, case-insensitively after ``.strip()``, instead of
#: keeping its own hand-copied set. They had already drifted (logging's
#: falsey set lacked "n"/"f", so ``DUHO_TRACEBACK=n`` turned tracebacks ON
#: while every declared bool field and ``AGENT_HELP`` treated "n" as off).
BOOL_TRUE: frozenset = frozenset({"1", "true", "yes", "on", "y", "t"})
BOOL_FALSE: frozenset = frozenset({"0", "false", "no", "off", "n", "f", ""})


def get_level_names_mapping() -> dict[str, int]:
    """Get mapping of level names to level integers.

    Fallback for Python < 3.11, which lacks getLevelNamesMapping (added in
    3.11, not 3.10 -- C063).
    """
    if hasattr(_logging, "getLevelNamesMapping"):
        return _logging.getLevelNamesMapping()
    return _logging._nameToLevel.copy()


def iter_entry_points(group: str) -> "list":
    """Return the installed-distribution entry points in ``group`` (F6).

    Bridges the two ``importlib.metadata.entry_points`` shapes:

    * **3.10+** -- ``entry_points(group=...)`` accepts a ``group`` keyword and
      returns a selectable view of the matching entry points, already
      de-duplicated by distribution: when the same distribution name is
      visible more than once on ``sys.path`` (user site + venv, a stray
      ``.egg-info``/``.dist-info`` left in the CWD or on ``PYTHONPATH``), only
      the first copy found contributes its entry points.
    * **3.9** -- ``entry_points()`` takes no arguments, returns a plain
      ``dict`` keyed by group name, and does NOT de-duplicate by
      distribution. Left as-is, a duplicated distribution returned every
      entry point twice, which made ``duho.app``'s M6 collision registry log
      a bogus "registered by more than one source" WARNING on every
      invocation, help included (C035). Mirrors 3.10+'s own dedup here:
      iterate distributions directly, skip one whose normalized name was
      already seen (first copy on ``sys.path`` wins, matching 3.10+), and
      collect only the matching group's entry points from what's left.

    ``importlib.metadata`` is imported lazily *inside* this helper (never at
    module top) so a plain ``import duho`` never pays its import cost -- only an
    app that actually opts into ``entry_points=`` discovery triggers the load
    (startup budget, plan 02 P1).
    """
    import importlib.metadata as _md

    try:
        return list(_md.entry_points(group=group))
    except TypeError:
        # Python 3.9 fallback (see docstring above).
        import re as _re

        seen_names: "set[str]" = set()
        result: "list" = []
        for dist in _md.distributions():
            name = (dist.metadata or {}).get("Name")
            if name:
                normalized = _re.sub(r"[-_.]+", "-", name).lower()
                if normalized in seen_names:
                    continue
                seen_names.add(normalized)
            for ep in dist.entry_points:
                if ep.group == group:
                    result.append(ep)
        return result


__all__ = [
    "UNION_ORIGINS",
    "BOOL_TRUE",
    "BOOL_FALSE",
    "get_level_names_mapping",
    "iter_entry_points",
]
