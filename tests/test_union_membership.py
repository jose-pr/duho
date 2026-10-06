"""Tests that a Literal composed into a multi-member Union keeps its
membership check. `_union_spec` resolves each member through
`_factory_for` and must keep every member's `choices`, or
`Union[Literal["auto"], int]` accepts ANY text for the Literal member (and,
because `str`-like conversions rarely raise, could silently shadow a later
member). All classes are declared at module level in this real ``.py`` file
so their AST-derived flags resolve normally.
"""

import typing as ty

import pytest

import duho
from duho import Args


class _WorkersArgs(Args):
    """A common idiom: an int option with an "auto" escape hatch."""

    workers: "ty.Union[ty.Literal['auto'], int]" = "auto"
    ("--workers",)


def test_union_literal_first_int_value_is_int():
    inst = duho.parse(_WorkersArgs, ["--workers", "4"])
    assert inst.workers == 4
    assert isinstance(inst.workers, int)


def test_union_literal_first_declared_choice_is_accepted():
    assert duho.parse(_WorkersArgs, ["--workers", "auto"]).workers == "auto"


def test_union_literal_first_rejects_undeclared_text():
    # A bare `str` factory for the Literal member (no membership check)
    # would never raise on `str("banana")`, and the union try-loop would
    # accept it without ever trying `int`.
    with pytest.raises(SystemExit):
        duho.parse(_WorkersArgs, ["--workers", "banana"])


class _WorkersRevArgs(Args):
    """Same idiom, member order reversed."""

    workers: "ty.Union[int, ty.Literal['auto']]" = "auto"
    ("--workers",)


def test_union_literal_last_int_value_is_int():
    assert duho.parse(_WorkersRevArgs, ["--workers", "4"]).workers == 4


def test_union_literal_last_declared_choice_is_accepted():
    assert duho.parse(_WorkersRevArgs, ["--workers", "auto"]).workers == "auto"


def test_union_literal_last_rejects_undeclared_text():
    with pytest.raises(SystemExit):
        duho.parse(_WorkersRevArgs, ["--workers", "banana"])
