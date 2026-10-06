"""Tests for the flag-between-positionals reorder fix.

argparse's own greedy positional-run matching (bpo-15112) breaks when an
optional flag sits BETWEEN a fixed positional and a variable-arity
(``nargs`` in ``"*"``/``"+"``/``"?"``) one in the same parser: the run gets
settled against the argv slice before the next optional, so the variadic
positional closes out empty/short and never reopens. Verified bare-stdlib
(no duho) before this fix existed. duho now reorders recognized flags to the
front of the argv slice a risky parser will see, so all four argument orderings
(flag-before/-after/-between the positionals, or no flag at all) parse
identically -- while a genuine typo'd flag still surfaces argparse's own
honest "unrecognized arguments" error, never silently swallowed as a
phantom positional value.

Every ``Args`` fixture is declared directly in this module's own source (AST-
based introspection needs real on-disk source, never a ``python -c`` string
-- matching this project's existing test convention).
"""

import duho
from duho import Arg, Args, NS
from duho.args import (
    _has_variadic_positional,
    _reorder_argv_for_variadic_positional,
)


class QueryArgs(Args):
    """A fan-out-style grammar: <ns> [TARGET ...], with a -f flag."""

    ns: str
    "The namespace/method to query."
    ("ns",)

    targets: "list[str]" = []
    "Trailing target hosts."
    ("targets",)

    filters: "Arg[list, NS(action='append', nargs=None)]" = []
    "Repeatable key=value filters -- nargs=None pins ONE value per flag."
    ("-f",)


class SinglePositionalArgs(Args):
    """No sibling positional -- NOT the risky shape."""

    targets: "list[str]" = []
    ("targets",)

    verbose: bool = False
    ("-v",)


class FixedArityOnlyArgs(Args):
    """Two positionals, but BOTH fixed-arity -- NOT the risky shape."""

    first: str
    ("first",)

    second: str
    ("second",)

    verbose: bool = False
    ("-v",)


class SubcommandRoot(Args):
    """A root with subcommands -- the subparsers action must not false-trigger."""

    _subcommands_ = []


# --------------------------------------------------------------------------
# Shape detection
# --------------------------------------------------------------------------


def test_shape_detected_for_fixed_then_variadic_positionals():
    parser = QueryArgs._parser_()
    assert _has_variadic_positional(parser) is True


def test_shape_detected_for_single_variadic_positional():
    """A LONE variadic positional (no sibling) is ALSO the risky shape:
    a flag touching its own run gets swallowed as "unrecognized arguments"
    (bpo-14191) just like the two-positional case does -- see
    `test_lone_variadic_positional_with_flag_inside` below."""
    parser = SinglePositionalArgs._parser_()
    assert _has_variadic_positional(parser) is True


def test_shape_not_detected_for_fixed_arity_positionals_only():
    parser = FixedArityOnlyArgs._parser_()
    assert _has_variadic_positional(parser) is False


def test_subparsers_action_does_not_false_trigger():
    """A root with `_subcommands_` and no OTHER declared positional must not
    treat the subparsers action itself as a risky sibling positional."""
    parser = SubcommandRoot._parser_()
    assert _has_variadic_positional(parser) is False


# --------------------------------------------------------------------------
# The reorder fix, exercised end-to-end through duho.parse
# --------------------------------------------------------------------------


def test_no_flag_baseline():
    result = duho.parse(QueryArgs, ["user", "nas1", "nas2"])
    assert result.ns == "user"
    assert result.targets == ["nas1", "nas2"]
    assert result.filters == []


def test_flag_before_positionals():
    result = duho.parse(QueryArgs, ["-f", "username=root", "user", "nas1"])
    assert result.ns == "user"
    assert result.targets == ["nas1"]
    assert result.filters == ["username=root"]


def test_flag_after_positionals():
    result = duho.parse(QueryArgs, ["user", "nas1", "-f", "username=root"])
    assert result.ns == "user"
    assert result.targets == ["nas1"]
    assert result.filters == ["username=root"]


def test_flag_between_positionals_the_original_bug():
    """The exact shape that broke before this fix: `<ns> -f <val> <targets>`."""
    result = duho.parse(QueryArgs, ["user", "-f", "username=root", "nas1"])
    assert result.ns == "user"
    assert result.targets == ["nas1"]
    assert result.filters == ["username=root"]


def test_flag_between_positionals_multiple_targets():
    result = duho.parse(
        QueryArgs, ["user", "-f", "username=root", "nas1", "nas2", "nas3"]
    )
    assert result.ns == "user"
    assert result.targets == ["nas1", "nas2", "nas3"]
    assert result.filters == ["username=root"]


def test_flag_equals_value_form_between_positionals():
    result = duho.parse(QueryArgs, ["user", "-f=username=root", "nas1"])
    assert result.ns == "user"
    assert result.targets == ["nas1"]
    assert result.filters == ["username=root"]


def test_repeated_flag_between_positionals():
    result = duho.parse(QueryArgs, ["user", "-f", "a=1", "-f", "b=2", "nas1"])
    assert result.ns == "user"
    assert result.targets == ["nas1"]
    assert result.filters == ["a=1", "b=2"]


# --------------------------------------------------------------------------
# The hard invariant: never silently swallow a genuine typo
# --------------------------------------------------------------------------


def test_typo_flag_still_raises_unrecognized_arguments():
    """A typo'd flag must surface argparse's own honest error, never be
    silently absorbed as phantom positional values."""
    import pytest

    with pytest.raises(SystemExit):
        duho.parse(QueryArgs, ["user", "--filtr", "x", "nas1"])


def test_malformed_trailing_flag_with_no_value_raises(capsys):
    """A flag needing a value, with none left in argv, still errors (bail,
    don't guess)."""
    import pytest

    with pytest.raises(SystemExit):
        duho.parse(QueryArgs, ["user", "nas1", "-f"])


# --------------------------------------------------------------------------
# Interaction with `_passthrough_` (argv after the first literal `--`)
# --------------------------------------------------------------------------


def test_reorder_and_passthrough_compose():
    """Reordering must only ever touch the PRE-`--` slice; everything after
    the first `--` stays untouched, exactly as `_passthrough_` already
    contracts."""
    result = duho.parse(
        QueryArgs,
        ["user", "-f", "username=root", "nas1", "--", "extra", "--not-a-flag"],
    )
    assert result.ns == "user"
    assert result.targets == ["nas1"]
    assert result.filters == ["username=root"]
    assert result._passthrough_ == ["extra", "--not-a-flag"]


# --------------------------------------------------------------------------
# The variadic-nargs-FLAG ambiguity is out of scope -- bail, don't guess
# --------------------------------------------------------------------------


class VariadicFlagArgs(Args):
    """A flag whose OWN nargs is variable (via an EXPLICIT `NS(nargs="*")`
    override -- a bare `list[T]`-as-option now defaults to `nargs=None`,
    see the `list[T]`-as-option default change) -- confirmed this session
    that even bare argparse cannot resolve this combination when the flag
    sits directly before positional values, with no separator. The reorder
    pass must bail (not guess) for this shape."""

    ns: str
    ("ns",)

    targets: "list[str]" = []
    ("targets",)

    # An EXPLICIT NS(nargs="*") is now the only way to reach this shape for
    # an option -- the bare list[T] default is nargs=None (see above).
    filters: "Arg[list, NS(action='extend', nargs='*')]" = []
    ("-f",)


def test_variadic_nargs_flag_bails_unreordered_not_worse_than_baseline():
    """Confirmed: this exact shape misparses identically whether or not
    duho's reorder pass touches it (verified against bare argparse with
    correctly-pre-ordered argv, same misparse) -- the fix must not make an
    already-ambiguous shape WORSE by guessing where the flag's variadic
    consumption ends."""
    parser = VariadicFlagArgs._parser_()
    argv = ["user", "-f", "username=root", "nas1"]
    reordered = _reorder_argv_for_variadic_positional(parser, list(argv))
    assert reordered == argv  # bailed, unchanged


# --------------------------------------------------------------------------
# A lone variadic positional (no sibling) is ALSO the risky shape
# --------------------------------------------------------------------------


def test_lone_variadic_positional_with_flag_inside():
    """The exact shape: a flag placed touching a LONE variadic
    positional's own run, with no sibling positional at all. This used to be
    swallowed as "unrecognized arguments" (bpo-14191) even though a previous
    version of the gate's docstring claimed a lone variadic positional was
    unaffected."""
    result = duho.parse(SinglePositionalArgs, ["a.txt", "-v", "b.txt"])
    assert result.targets == ["a.txt", "b.txt"]
    assert result.verbose is True


# --------------------------------------------------------------------------
# Attached short-option values and unambiguous long prefixes
# --------------------------------------------------------------------------


class ShortFlagArgs(Args):
    """Same shape as `QueryArgs`, but with a long alias on the `-f` flag so
    an unambiguous `--filt` prefix has something to resolve to."""

    ns: str
    ("ns",)

    targets: "list[str]" = []
    ("targets",)

    filters: "Arg[list, NS(action='append', nargs=None)]" = []
    ("-f", "--filter")


def test_attached_short_option_value_between_positionals():
    """`-fVALUE` (the value glued directly onto a short flag) between two
    positionals -- one of argparse's own accepted spellings, previously
    missed by the reorder pass's exact-key/`--flag=value` recognition."""
    result = duho.parse(ShortFlagArgs, ["user", "-fusername=root", "nas1"])
    assert result.ns == "user"
    assert result.targets == ["nas1"]
    assert result.filters == ["username=root"]


def test_unambiguous_long_prefix_between_positionals():
    """`--filt` (an unambiguous prefix of `--filter`, argparse's own
    `allow_abbrev` rule) between two positionals."""
    result = duho.parse(ShortFlagArgs, ["user", "--filt", "username=root", "nas1"])
    assert result.ns == "user"
    assert result.targets == ["nas1"]
    assert result.filters == ["username=root"]


def test_unambiguous_long_prefix_with_attached_value_between_positionals():
    """`--filt=value` (an unambiguous prefix of `--filter`, WITH the value
    attached via `=`) between two positionals -- previously missed: the
    reorder pass's exact-key `--flag=value` split never matches an
    abbreviated key, and the abbreviation branch itself explicitly excluded
    any token containing `=`, so this fell through as unrecognized and the
    whole reorder bailed."""
    result = duho.parse(ShortFlagArgs, ["user", "--filt=username=root", "nas1"])
    assert result.ns == "user"
    assert result.targets == ["nas1"]
    assert result.filters == ["username=root"]


# --------------------------------------------------------------------------
# Negative numbers are values, not flags
# --------------------------------------------------------------------------


class DigitFlagArgs(Args):
    """A parser with a negative-number-shaped option: `-5` is then a flag."""

    ns: str
    ("ns",)

    targets: "list[str]" = []
    ("targets",)

    five: bool = False
    ("-5",)

    filters: "Arg[list, NS(action='append', nargs=None)]" = []
    ("-f",)


def test_negative_number_positional_in_every_ordering():
    expected_filters = ["a=b"]
    for argv in (
        ["q", "-5", "h2", "-f", "a=b"],
        ["-f", "a=b", "q", "-5", "h2"],
        ["q", "-f", "a=b", "-5", "h2"],
        ["q", "-5", "-f", "a=b", "h2"],
    ):
        result = duho.parse(QueryArgs, argv)
        assert result.ns == "q", argv
        assert result.targets == ["-5", "h2"], argv
        assert result.filters == expected_filters, argv


def test_negative_number_positional_without_a_flag():
    result = duho.parse(QueryArgs, ["q", "-5", "h2"])
    assert result.targets == ["-5", "h2"]


def test_declared_negative_number_option_is_hoisted_like_any_flag():
    parser = DigitFlagArgs._parser_()
    argv = ["q", "-5", "-f", "a=b", "h2"]
    assert _reorder_argv_for_variadic_positional(parser, argv) == [
        "-5",
        "-f",
        "a=b",
        "q",
        "h2",
    ]
    result = duho.parse(DigitFlagArgs, argv)
    assert result.five is True
    assert result.filters == ["a=b"]


def test_other_negative_number_is_left_alone_when_the_parser_declares_one():
    """With a negative-number option declared, an unknown `-7` is ambiguous:
    the reorder gets out of the way and argparse reports it."""
    parser = DigitFlagArgs._parser_()
    argv = ["q", "-7", "-f", "a=b", "h2"]
    assert _reorder_argv_for_variadic_positional(parser, list(argv)) == argv
