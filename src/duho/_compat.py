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


__all__ = ["UNION_ORIGINS", "get_level_names_mapping"]
