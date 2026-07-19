"""Phase D regression tests: parsers per-instance surgery (M1) + pop_action (M20)."""

import argparse

import pytest

import duho
from duho import Arg, Cli, Cmd
from duho.parsers import pop_action, prerun_parse


# -- M1: no argparse class-global mutation, works with a subparser tree -------


def test_prerun_parse_leaves_argparse_classes_untouched():
    help_before = argparse._HelpAction.__call__
    sub_before = argparse._SubParsersAction.__call__

    parser = argparse.ArgumentParser()
    parser.add_argument("--flag")
    subs = parser.add_subparsers(dest="command")
    child = subs.add_parser("go")
    child.add_argument("--n", type=int)

    # A parser WITH subparsers must return globals and never mutate the classes.
    result = prerun_parse(parser, ["--flag", "x", "go", "--n", "3"])
    assert result.flag == "x"
    assert argparse._HelpAction.__call__ is help_before
    assert argparse._SubParsersAction.__call__ is sub_before
    # The action instance's class is restored, not left as the relaxed subclass.
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            assert type(action) is argparse._SubParsersAction


def test_prerun_parse_help_does_not_exit_and_restores():
    help_before = argparse._HelpAction.__call__
    parser = argparse.ArgumentParser()
    parser.add_argument("--flag")
    # --help must NOT SystemExit during the prepass.
    result = prerun_parse(parser, ["--help"])
    assert result is not None
    assert argparse._HelpAction.__call__ is help_before
    # The parser's own help still works afterwards (class restored).
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])


def test_prerun_parse_sequential_calls():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flag")
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("go")
    a = prerun_parse(parser, ["--flag", "1", "go"])
    b = prerun_parse(parser, ["--flag", "2", "go"])
    assert a.flag == "1"
    assert b.flag == "2"


# -- M20: pop_action removes the flag from format_help ------------------------


def test_pop_action_removes_from_help():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gone", help="should disappear")
    parser.add_argument("--kept", help="stays")
    pop_action(parser, "gone")
    help_text = parser.format_help()
    assert "--gone" not in help_text
    assert "--kept" in help_text
