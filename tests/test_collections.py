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
from duho import Arg, Args, Choice, NS

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


class ExplicitListNargsStarArgs(Args):
    """Same explicit `NS(nargs="*")` opt-back, on a `list[T]` field this
    time: the option-vs-positional nargs/action shape is now decided from
    the FINAL flags and nargs once every override is applied, so this
    explicit opt-back restores space-separated multi-value instead of
    downgrading to one-value-per-occurrence and nesting."""

    xs: "Arg[list[str], NS(nargs='*')]"
    ("--xs",)


def test_list_option_explicit_nargs_star_restores_space_separated():
    inst = duho.parse(ExplicitListNargsStarArgs, ["--xs", "a", "b", "--xs", "c"])
    assert inst.xs == ["a", "b", "c"]


# --- CLI wins over a layered default, for every collection kind (A005) ----


class ListNonEmptyDefaultArgs(Args):
    """A list option with a non-empty class default."""

    paths: "list[str]" = ["default"]
    ("--paths",)


def test_list_cli_value_replaces_nonempty_class_default():
    inst = duho.parse(ListNonEmptyDefaultArgs, ["--paths", "cli"])
    assert inst.paths == ["cli"]


def test_list_repeated_flag_still_accumulates_after_replacing():
    inst = duho.parse(ListNonEmptyDefaultArgs, ["--paths", "a", "--paths", "b"])
    assert inst.paths == ["a", "b"]


class EnvListArgs(Args):
    """A list option layered from an env var."""

    tags: "Arg[list[str], NS(env='DUHO_TEST_A005_TAGS')]" = []
    ("--tags",)


def test_list_cli_value_replaces_env_default(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_A005_TAGS", "fromenv")
    inst = duho.parse(EnvListArgs, ["--tags", "cli"])
    assert inst.tags == ["cli"]


def test_list_config_value_replaced_by_cli(tmp_path):
    cfg = tmp_path / "cfg.toml"
    cfg.write_text('paths = ["a", "b"]\n')
    inst = duho.parse(ListNonEmptyDefaultArgs, ["--paths", "c"], config=cfg)
    assert inst.paths == ["c"]


# --- A006: a zero-token variadic POSITIONAL never crashes or duplicates ---


class SetPositionalArgs(Args):
    """A `set[str]` positional -- a zero-token nargs='*' positional used to
    crash (`set([<the default set>])`, unhashable)."""

    tags: "set[str]"
    ("tags",)


def test_set_positional_with_zero_tokens_gives_empty_set():
    inst = duho.parse(SetPositionalArgs, [])
    assert inst.tags == set()


class ListPositionalDefaultArgs(Args):
    """A `list[str]` positional with a non-empty default -- a zero-token
    nargs='*' positional used to double it (`['x', 'x']`)."""

    items: "list[str]" = ["x"]
    ("items",)


def test_list_positional_with_zero_tokens_does_not_double_default():
    inst = duho.parse(ListPositionalDefaultArgs, [])
    assert inst.items == ["x"]


def test_list_positional_with_tokens_replaces_default():
    inst = duho.parse(ListPositionalDefaultArgs, ["a"])
    assert inst.items == ["a"]


# --- A021: a parser reused across multiple parse_args() calls does not ----
# --- share (and leak mutations through) a list/set/dict default -----------


class MutableDefaultArgs(Args):
    tags: "list[str]" = []
    ("--tags",)


def test_mutable_default_not_shared_across_parses_of_one_parser():
    parser = MutableDefaultArgs._parser_()
    a = parser.parse_args([])
    b = parser.parse_args([])
    assert a.tags is not b.tags
    a.tags.append("leak")
    c = parser.parse_args([])
    assert c.tags == []


# --- A049: a variadic list positional with Choice() can be omitted --------


class ChoiceListPositionalArgs(Args):
    """A `list[str]` positional restricted to `Choice(...)` -- argparse
    through 3.13 validated the EMPTY default against `choices` too
    (bpo-9625), so omitting it raised "invalid choice: []" instead of using
    the declared empty list."""

    formats: "Arg[list[str], Choice('json', 'csv')]" = []
    ("formats",)


def test_variadic_choice_positional_can_be_omitted():
    inst = duho.parse(ChoiceListPositionalArgs, [])
    assert inst.formats == []


def test_variadic_choice_positional_still_validates_given_values():
    inst = duho.parse(ChoiceListPositionalArgs, ["json"])
    assert inst.formats == ["json"]
    with pytest.raises(SystemExit):
        duho.parse(ChoiceListPositionalArgs, ["xml"])
