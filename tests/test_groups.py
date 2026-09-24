"""Tests for required mutually-exclusive groups (F2) and titled argument
groups (F3).

All classes are declared at module level so the AST-derived flag tuples
resolve from a real file.
"""

import argparse

import pytest

from duho import Arg, Args, Cmd, NS

# --- F2: required mutually-exclusive groups ------------------------------


class RequiredExclusive(Args):
    """Two flags in a required mutually-exclusive group."""

    push: Arg[bool, NS(conflicts="mode", conflicts_required=True)] = False
    "Push mode"
    ("--push",)

    pull: Arg[bool, NS(conflicts="mode")] = False
    "Pull mode"
    ("--pull",)


def test_required_group_omitting_all_errors():
    parser = RequiredExclusive._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_required_group_one_member_passes():
    parser = RequiredExclusive._parser_()
    args = parser.parse_args(["--push"])
    assert args.push is True
    assert args.pull is False


def test_required_group_two_members_conflict():
    parser = RequiredExclusive._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--push", "--pull"])


def test_required_group_marked_required_on_parser():
    parser = RequiredExclusive._parser_()
    group = parser.exclusive_groups["mode"]
    assert group.required is True


class OptionalExclusive(Args):
    """A plain (not required) mutually-exclusive group."""

    a: Arg[bool, NS(conflicts="g")] = False
    ("--a",)

    b: Arg[bool, NS(conflicts="g")] = False
    ("--b",)


def test_optional_group_allows_none():
    parser = OptionalExclusive._parser_()
    args = parser.parse_args([])
    assert args.a is False and args.b is False
    assert parser.exclusive_groups["g"].required is False


# --- F3: titled argument groups ------------------------------------------


class Grouped(Args):
    """Fields bucketed into a titled argument group."""

    outfile: Arg[str, NS(group="Output options")] = "-"
    "Where to write"
    ("--outfile",)

    verbose_out: Arg[bool, NS(group="Output options")] = False
    "Verbose output"
    ("--verbose-out",)

    infile: str = "-"
    "Where to read"
    ("--infile",)


def test_group_section_in_help():
    parser = Grouped._parser_()
    help_text = parser.format_help()
    assert "Output options:" in help_text


def test_group_still_parses_normally():
    parser = Grouped._parser_()
    args = parser.parse_args(["--outfile", "x", "--infile", "y"])
    assert args.outfile == "x"
    assert args.infile == "y"


class GroupedAndExclusive(Args):
    """A field with both group= and conflicts= (nested exclusive group)."""

    json_out: Arg[bool, NS(group="Format", conflicts="fmt")] = False
    "JSON output"
    ("--json",)

    yaml_out: Arg[bool, NS(group="Format", conflicts="fmt")] = False
    "YAML output"
    ("--yaml",)


def test_grouped_and_conflicting():
    parser = GroupedAndExclusive._parser_()
    help_text = parser.format_help()
    assert "Format:" in help_text
    # Still mutually exclusive.
    args = parser.parse_args(["--json"])
    assert args.json_out is True
    with pytest.raises(SystemExit):
        parser.parse_args(["--json", "--yaml"])


# --- A023: a conflicts= member without a default is not forced required ---


class ExclusiveNoDefaults(Args):
    """Two exclusive selectors, NEITHER declaring a default -- the natural
    way to write `--name NAME | --id ID`. The generic "no default -> required"
    rule used to force `required=True` on a mutex-group member, which
    argparse itself forbids ("mutually exclusive arguments must be
    optional"), failing the parser BUILD with a message naming neither
    field."""

    name: Arg[str, NS(conflicts="who")]
    "Name selector"
    ("--name",)

    ident: Arg[int, NS(conflicts="who")]
    "Id selector"
    ("--id",)


def test_conflicts_member_without_default_builds_successfully():
    parser = ExclusiveNoDefaults._parser_()
    args = parser.parse_args(["--name", "bob"])
    assert args.name == "bob"
    assert args.ident is None


def test_conflicts_member_without_default_still_conflicts():
    parser = ExclusiveNoDefaults._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--name", "bob", "--id", "5"])


# --- A024: a conflicts= key must use the same group= everywhere -----------


class ConflictsAcrossTitles(Args):
    """The SAME conflicts= key under two different group= titles -- members
    silently landed in TWO separate mutex groups (one per title) and were no
    longer mutually exclusive at all."""

    a: Arg[bool, NS(conflicts="x", group="A")] = False
    ("--a",)

    b: Arg[bool, NS(conflicts="x", group="B")] = False
    ("--b",)


def test_conflicts_key_across_different_titles_raises_at_build():
    with pytest.raises(ValueError, match="conflicts"):
        ConflictsAcrossTitles._parser_()


class ConflictsSameTitleFine(Args):
    """The SAME conflicts= key under the SAME group= title is fine."""

    a: Arg[bool, NS(conflicts="x", group="A")] = False
    ("--a",)

    b: Arg[bool, NS(conflicts="x", group="A")] = False
    ("--b",)


def test_conflicts_key_under_same_title_still_conflicts():
    parser = ConflictsSameTitleFine._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--a", "--b"])


# --- parser.exclusive_groups is reachable from a _parser_ override ---------


class _ConflictCmd(Cmd):
    """A command with a conflicts=-built mutually-exclusive group."""

    type: Arg[str, NS(conflicts="type")] = "-"
    ("--type", "-t")

    def __call__(self):
        return 0


def test_exclusive_groups_exposed_on_parser():
    parser = _ConflictCmd._parser_()
    assert hasattr(parser, "exclusive_groups")
    assert "type" in parser.exclusive_groups
    group = parser.exclusive_groups["type"]
    assert isinstance(group, argparse._MutuallyExclusiveGroup)


def test_override_can_add_into_exclusive_group():
    class _OverrideCmd(_ConflictCmd):
        @classmethod
        def _parser_(cls, subparser=None, name=None, parents=(), **kwargs):
            parser = super()._parser_(subparser, name, parents, **kwargs)
            parser.exclusive_groups["type"].add_argument(
                "-d", dest="d", action="store_true", default=False
            )
            return parser

    parser = _OverrideCmd._parser_()
    # -t and -d now live in the same mutually-exclusive group.
    ns = parser.parse_args(["-d"])
    assert ns.d is True
    with pytest.raises(SystemExit):
        parser.parse_args(["-t", "x", "-d"])
