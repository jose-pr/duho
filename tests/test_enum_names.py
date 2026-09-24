"""Regression tests for A053: `_enum_name_factory` validated by iterating
the enum, which skips ALIASES (an alias name would never be recognized) and,
since Python 3.11, skips multi-bit `Flag` composite members too (so the same
declaration accepts a composite name on 3.9 but rejects it on 3.11+).
Validating against `enum_cls.__members__` (which always includes both) fixes
this uniformly across the supported version range.

All classes are declared at module level in this real ``.py`` file so their
AST-derived flags resolve normally.
"""

import enum

import duho
from duho import Args


class _AliasColor(enum.Enum):
    RED = 1
    CRIMSON = 1  # alias of RED (same value)
    GREEN = 2


class _AliasArgs(Args):
    c: "_AliasColor" = _AliasColor.RED
    ("--c",)


def test_enum_alias_name_is_accepted():
    assert duho.parse(_AliasArgs, ["--c", "CRIMSON"]).c is _AliasColor.RED


def test_enum_canonical_name_still_accepted():
    assert duho.parse(_AliasArgs, ["--c", "RED"]).c is _AliasColor.RED


class _Perm(enum.Flag):
    R = 4
    W = 2
    RW = R | W


class _FlagArgs(Args):
    p: "_Perm" = _Perm.R
    ("--p",)


def test_flag_composite_member_name_is_accepted():
    # Confirmed-broken on 3.11+ pre-fix (enum.Flag iteration skips multi-bit
    # composites there, though not on 3.9), so this closes a version-dependent
    # gap rather than a universally-broken one.
    assert duho.parse(_FlagArgs, ["--p", "RW"]).p == _Perm.RW
