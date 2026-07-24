"""Tests for set/set[T] and tuple/tuple[T, ...] collection fields.

These mirror the list[T] tests: element-type conversion, defaults when
unset, plus set dedup and the fixed-length-tuple build-time error. All
classes are declared at module level so the AST-derived flag tuples resolve
from a real file (never via `python -c`).

A collection field used as an OPTION defaults to ONE value per flag
occurrence (`nargs=None`) -- repeat the flag for more (`--x a --x b`), not
space-separated in one occurrence (`--x a b`). `_factory_for`'s `"*"` default
is downgraded to `None` specifically for the option case (a POSITIONAL
collection field keeps `nargs="*"`, unrelated -- see `test_positional_reorder
.py`); pass an explicit `NS(nargs="*")` to opt back into space-separated
multi-value for a specific option field.
"""

import pytest

import duho
from duho import Arg, Args, NS


# --- set / set[T] fields -------------------------------------------------


class SetArgs(Args):
    """Arguments with set fields."""

    tags: set
    "Bare set of strings"
    ("--tags",)

    numbers: "set[int]" = None
    "Typed set of ints"
    ("--numbers",)


def test_set_accumulation_repeated_flag():
    """Repeated `--x a --x b` accumulates into a set."""
    parser = SetArgs._parser_()
    args = parser.parse_args(["--tags", "a", "--tags", "b"])
    assert args.tags == {"a", "b"}
    assert isinstance(args.tags, set)


def test_set_option_rejects_space_separated_multi_value():
    """An option field takes ONE value per occurrence -- `--x a b` leaves `b`
    unconsumed, surfacing as an unrecognized positional (this is what makes
    a repeatable option safe to place between two positionals -- see
    `test_positional_reorder.py`)."""
    parser = SetArgs._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--tags", "a", "b"])


def test_set_dedups():
    """A set field dedups repeated values."""
    parser = SetArgs._parser_()
    args = parser.parse_args(
        ["--numbers", "1", "--numbers", "2", "--numbers", "2", "--numbers", "1"]
    )
    assert args.numbers == {1, 2}


def test_set_default_empty_when_undeclared():
    """A set field with no explicit default gets an empty set."""
    parser = SetArgs._parser_()
    args = parser.parse_args([])
    assert args.tags == set()
    assert isinstance(args.tags, set)


def test_set_element_type_conversion():
    """set[int] converts each element with the element factory."""
    parser = SetArgs._parser_()
    args = parser.parse_args(["--numbers", "1", "--numbers", "2", "--numbers", "3"])
    assert args.numbers == {1, 2, 3}
    assert all(isinstance(n, int) for n in args.numbers)


def test_bare_set_elements_are_str():
    """Bare `set` (unparameterized) collects str elements."""
    parser = SetArgs._parser_()
    args = parser.parse_args(["--tags", "1", "--tags", "2"])
    assert args.tags == {"1", "2"}
    assert all(isinstance(t, str) for t in args.tags)


# --- tuple / tuple[T, ...] fields ----------------------------------------


class TupleArgs(Args):
    """Arguments with tuple fields."""

    raw: tuple
    "Bare tuple of strings"
    ("--raw",)

    nums: "tuple[int, ...]" = None
    "Variadic homogeneous tuple of ints"
    ("--nums",)


def test_tuple_accumulation_repeated_flag():
    """Repeated `--x a --x b` accumulates into a tuple in order."""
    parser = TupleArgs._parser_()
    args = parser.parse_args(["--raw", "a", "--raw", "b"])
    assert args.raw == ("a", "b")
    assert isinstance(args.raw, tuple)


def test_tuple_option_rejects_space_separated_multi_value():
    """An option field takes ONE value per occurrence -- `--x a b` leaves `b`
    unconsumed, surfacing as an unrecognized positional."""
    parser = TupleArgs._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--raw", "a", "b"])


def test_tuple_preserves_order_and_duplicates():
    """A tuple keeps insertion order and does NOT dedup (unlike set)."""
    parser = TupleArgs._parser_()
    args = parser.parse_args(
        ["--nums", "3", "--nums", "1", "--nums", "1", "--nums", "2"]
    )
    assert args.nums == (3, 1, 1, 2)


def test_tuple_default_empty_when_undeclared():
    """A tuple field with no explicit default gets an empty tuple."""
    parser = TupleArgs._parser_()
    args = parser.parse_args([])
    assert args.raw == ()
    assert isinstance(args.raw, tuple)


def test_tuple_element_type_conversion():
    """tuple[int, ...] converts each element with the element factory."""
    parser = TupleArgs._parser_()
    args = parser.parse_args(["--nums", "1", "--nums", "2", "--nums", "3"])
    assert args.nums == (1, 2, 3)
    assert all(isinstance(n, int) for n in args.nums)


def test_bare_tuple_elements_are_str():
    """Bare `tuple` (unparameterized) collects str elements."""
    parser = TupleArgs._parser_()
    args = parser.parse_args(["--raw", "1", "--raw", "2"])
    assert args.raw == ("1", "2")
    assert all(isinstance(t, str) for t in args.raw)


# --- fixed-length tuple[A, B] -> clear build-time error ------------------


class FixedTupleArgs(Args):
    """A fixed-length heterogeneous tuple field (unsupported)."""

    pair: "tuple[int, str]"
    "Fixed-length heterogeneous tuple"
    ("--pair",)


def test_fixed_length_tuple_raises_clear_error():
    """tuple[A, B] raises a clear ValueError at parser build, naming the field."""
    with pytest.raises(ValueError) as excinfo:
        FixedTupleArgs._parser_()
    msg = str(excinfo.value)
    assert "pair" in msg
    assert "tuple[T, ...]" in msg


# --- end-to-end via duho.parse -------------------------------------------


def test_set_and_tuple_round_trip_via_parse():
    """duho.parse produces set/tuple field values end to end."""

    class Coll(Args):
        s: "set[int]"
        ("--s",)
        t: "tuple[str, ...]"
        ("--t",)

    inst = duho.parse(
        Coll, ["--s", "1", "--s", "2", "--s", "2", "--t", "x", "--t", "y"]
    )
    assert inst.s == {1, 2}
    assert inst.t == ("x", "y")


class ExplicitNargsStarArgs(Args):
    """An explicit `NS(nargs="*")` opts back into space-separated multi-value
    for a specific option field -- the escape hatch for anyone who wants the
    old behavior on a field they control."""

    s: "Arg[set[int], NS(nargs='*')]"
    ("--s",)


def test_set_option_explicit_nargs_star_restores_space_separated():
    inst = duho.parse(ExplicitNargsStarArgs, ["--s", "1", "2", "2"])
    assert inst.s == {1, 2}
