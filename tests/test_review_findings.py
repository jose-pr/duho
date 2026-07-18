"""Regression tests for the 2026-07-18 review findings (buildutils consumer).

1. A literal ``%`` in a Cmd docstring must not crash parser build.
2. ``parser.exclusive_groups`` must be reachable from a ``_parser_`` override.
3. Constructing a Cmd directly must seed declared field defaults (esp. bools).
"""

import argparse

import pytest

import duho
from duho import Arg, Args, Cli, Cmd, NS


# -- Finding 1: docstring % ---------------------------------------------------


class _PercentDoc(Cmd):
    """Dump the DB into a manifest (e.g. an RPM %files list)."""

    def __call__(self):
        return 0


class _PercentApp(Cli):
    _subcommands_ = [_PercentDoc]

    def __call__(self):
        return 0


def test_docstring_percent_does_not_crash_parser_build():
    # Previously raised ValueError("badly formed help string") from argparse
    # _check_help at add_parser time.
    parser = _PercentApp._parser_()
    text = parser.format_help()
    assert parser is not None
    # The subcommand help renders the literal text (argparse un-escapes %%).
    sub_help = _PercentDoc._parser_().format_help()
    assert "%files" in sub_help


def test_docstring_percent_help_runs():
    with pytest.raises(SystemExit) as exc:
        duho.main(_PercentApp, ["--help"])
    assert exc.value.code == 0


# -- Finding 2: exclusive_groups reachable from override ----------------------


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


# -- Finding 3: direct construction seeds declared defaults -------------------


class _Flags(Cmd):
    verbose: bool  # ("--verbose",) store_true, implicit default False
    ("--verbose",)
    name: str = "world"
    ("--name",)
    count: int = 3
    ("--count",)

    def __call__(self):
        return 0


def test_direct_construction_seeds_bool_default():
    inst = _Flags()
    # store_true bool with no assigned default -> False, even without argv.
    assert inst.verbose is False
    assert inst.name == "world"
    assert inst.count == 3


def test_direct_construction_passed_values_win():
    inst = _Flags(verbose=True, name="x")
    assert inst.verbose is True
    assert inst.name == "x"
    assert inst.count == 3  # still seeded


def test_parsed_values_not_shadowed_by_seeding():
    # The parse path constructs cls(**parsed.__dict__); seeding must fill only
    # gaps, never overwrite a parsed value.
    parser = _Flags._parser_()
    inst = parser.parse_args(["--verbose", "--name", "y", "--count", "9"])
    assert inst.verbose is True
    assert inst.name == "y"
    assert inst.count == 9


def test_self_clone_via_get_kwargs_has_full_surface():
    # The buildutils self-cloning pattern: type(self)(**self._get_kwargs()).
    inst = _Flags(name="z")
    clone = type(inst)(**dict(inst._get_kwargs()))
    assert clone.verbose is False
    assert clone.name == "z"
    assert clone.count == 3


# -- Finding 3: global option before subcommand not shadowed -------------------


class _RootBase(Cmd):
    db: str = None
    ("--db",)


class _RootSub(_RootBase):
    def __call__(self):
        return 0


class _RootApp(_RootBase, Cli):
    _subcommands_ = [_RootSub]

    def __call__(self):
        return 0


def test_global_option_before_subcommand_survives():
    parser = _RootApp._parser_()
    # Given BEFORE the subcommand -> previously clobbered to None by the child's
    # inherited --db default. Now preserved.
    assert parser.parse_args(["--db", "X", "_RootSub"]).db == "X"


def test_global_option_after_subcommand_still_works():
    parser = _RootApp._parser_()
    assert parser.parse_args(["_RootSub", "--db", "Y"]).db == "Y"


def test_global_option_absent_uses_root_default():
    parser = _RootApp._parser_()
    assert parser.parse_args(["_RootSub"]).db is None
