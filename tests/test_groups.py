"""Tests for required mutually-exclusive groups (F2) and titled argument
groups (F3).

All classes are declared at module level so the AST-derived flag tuples
resolve from a real file.
"""

import argparse

import pytest

from duho import Arg, Args, NS


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
