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


def get_level_names_mapping() -> dict[str, int]:
    """Get mapping of level names to level integers.

    Fallback for Python < 3.10 which lacks getLevelNamesMapping.
    """
    if hasattr(_logging, "getLevelNamesMapping"):
        return _logging.getLevelNamesMapping()
    return _logging._nameToLevel.copy()


def iter_entry_points(group: str) -> "list":
    """Return the installed-distribution entry points in ``group`` (F6).

    Bridges the two ``importlib.metadata.entry_points`` shapes:

    * **3.10+** -- ``entry_points(group=...)`` accepts a ``group`` keyword and
      returns a selectable view of the matching entry points.
    * **3.9** -- ``entry_points()`` takes no arguments and returns a ``dict``
      keyed by group name; select ``group`` out of it.

    ``importlib.metadata`` is imported lazily *inside* this helper (never at
    module top) so a plain ``import duho`` never pays its import cost -- only an
    app that actually opts into ``entry_points=`` discovery triggers the load
    (startup budget, plan 02 P1).
    """
    import importlib.metadata as _md

    try:
        return list(_md.entry_points(group=group))
    except TypeError:
        # Python 3.9: entry_points() takes no kwargs and returns {group: [...]}.
        return list(_md.entry_points().get(group, []))


__all__ = ["UNION_ORIGINS", "get_level_names_mapping", "iter_entry_points"]
