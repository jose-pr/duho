"""Tests that `_enum_name_factory` validates against `enum_cls.__members__`,
not by iterating the enum: iteration skips ALIASES (an alias name would
never be recognized) and, since Python 3.11, multi-bit `Flag` composite
members too (so the same declaration would accept a composite name on 3.9
but reject it on 3.11+). `__members__` includes both, uniformly across the
supported version range.

All classes are declared at module level in this real ``.py`` file so their
AST-derived flags resolve normally.
"""

import enum
import typing as _t

import pytest

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
    # enum.Flag iteration skips multi-bit composites on 3.11+ (not on 3.9),
    # so this guards a version-dependent gap.
    assert duho.parse(_FlagArgs, ["--p", "RW"]).p == _Perm.RW


# --------------------------------------------------------------------------
# A `Literal[...]` of specific Enum MEMBERS (not a bare Enum annotation) must
# resolve by NAME, the same as `_enum_spec` does -- the enum CLASS itself
# is not the factory (it looks members up by VALUE), so the exact name the
# metavar/choices advertise must be accepted.
# --------------------------------------------------------------------------


class _LiteralColor(enum.Enum):
    RED = 1
    BLUE = 2
    GREEN = 3


class _LiteralEnumArgs(Args):
    # Only a SUBSET of the enum's members -- a Literal is allowed to narrow.
    c: "_t.Literal[_LiteralColor.RED, _LiteralColor.BLUE]" = _LiteralColor.RED
    ("--c",)


def test_literal_of_enum_members_accepts_the_member_name():
    assert duho.parse(_LiteralEnumArgs, ["--c", "RED"]).c is _LiteralColor.RED


def test_literal_of_enum_members_rejects_a_member_not_in_the_subset():
    with pytest.raises(SystemExit):
        duho.parse(_LiteralEnumArgs, ["--c", "GREEN"])


def test_literal_of_enum_members_metavar_shows_plain_names():
    help_text = _LiteralEnumArgs._parser_().format_help()
    assert "{RED,BLUE}" in help_text
