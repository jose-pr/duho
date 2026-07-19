"""Direct unit tests for duho.parsers helpers (Plan 03 T1).

Covers the helper functions and the per-instance surgery invariants that
``prerun_parse`` relies on, complementing the M1/M20 regression tests in
``test_fix_phase_d_parsers.py`` (no overlap):

* ``pop_action`` / ``insert_action`` / ``add_help_argument`` exercised directly;
* the surgery is restored even on an EXCEPTION path forced mid-parse -- the
  invariant is "no ``argparse`` class attribute differs after the call" AND every
  per-instance ``__class__`` swap is undone.
"""

import argparse

import pytest

from duho.parsers import (
    add_help_argument,
    disable_subparser_check,
    enable_subparser_check,
    insert_action,
    pop_action,
    prerun_parse,
)


# --------------------------------------------------------------------------
# pop_action / insert_action / add_help_argument
# --------------------------------------------------------------------------


def test_pop_action_removes_from_parsing_and_optionmap():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep")
    action = parser.add_argument("--gone")
    popped = pop_action(parser, "gone")
    assert popped is action
    assert popped not in parser._actions
    assert "--gone" not in parser._option_string_actions
    # Parsing no longer recognizes the removed flag.
    with pytest.raises(SystemExit):
        parser.parse_args(["--gone", "x"])


def test_pop_action_unknown_raises_keyerror():
    parser = argparse.ArgumentParser()
    with pytest.raises(KeyError):
        pop_action(parser, "nope")


def test_pop_then_insert_action_restores_flag():
    parser = argparse.ArgumentParser()
    parser.add_argument("--x")
    action = pop_action(parser, "x")
    assert "--x" not in parser._option_string_actions
    insert_action(parser, action)
    # The flag is parseable again after re-insertion.
    ns = parser.parse_args(["--x", "5"])
    assert ns.x == "5"


def test_add_help_argument_adds_working_help():
    parser = argparse.ArgumentParser(add_help=False)
    assert "-h" not in parser._option_string_actions
    action = add_help_argument(parser)
    assert isinstance(action, argparse._HelpAction)
    assert "-h" in parser._option_string_actions
    assert "--help" in parser._option_string_actions
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])


# --------------------------------------------------------------------------
# disable/enable_subparser_check round-trip
# --------------------------------------------------------------------------


def test_disable_enable_subparser_check_round_trip():
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("go")
    action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    original_cls = type(action)
    original_choices = action.choices

    disable_subparser_check(action)
    assert type(action) is not original_cls  # swapped to the relaxed subclass
    assert action.choices is None  # name check disabled

    enable_subparser_check(action)
    assert type(action) is original_cls
    assert action.choices is original_choices
    assert not hasattr(action, "_duho_saved_")
    assert not hasattr(action, "_duho_action_called")


# --------------------------------------------------------------------------
# Exception-path restoration
# --------------------------------------------------------------------------


def _snapshot_argparse_classes():
    return {
        "help": argparse._HelpAction.__call__,
        "sub": argparse._SubParsersAction.__call__,
    }


def test_exception_mid_parse_restores_all_surgery(monkeypatch):
    """If parse_known_args raises, the finally-block restores every swap.

    The invariant: no ``argparse`` class attribute differs afterwards, and the
    per-instance ``__class__`` of the help + subparser actions is back to the
    stock argparse type.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--flag")
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("go")

    sub_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    help_action = next(
        a for a in parser._actions if isinstance(a, argparse._HelpAction)
    )
    before = _snapshot_argparse_classes()
    sub_cls_before = type(sub_action)
    help_cls_before = type(help_action)

    def boom(*args, **kwargs):
        raise RuntimeError("forced mid-parse failure")

    monkeypatch.setattr(parser, "parse_known_args", boom)

    with pytest.raises(RuntimeError, match="forced mid-parse failure"):
        prerun_parse(parser, ["--flag", "x", "go"])

    # argparse's own classes are untouched.
    after = _snapshot_argparse_classes()
    assert after == before
    # Per-instance swaps are restored despite the exception.
    assert type(sub_action) is sub_cls_before
    assert type(help_action) is help_cls_before
    # The relaxed-subclass bookkeeping is cleaned up too.
    assert not hasattr(sub_action, "_duho_saved_")
    assert not hasattr(sub_action, "_duho_action_called")


def test_subparser_required_flag_restored_after_call():
    """prerun_parse restores the subparsers action's `required` flag."""
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("go")
    sub_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    assert sub_action.required is True
    prerun_parse(parser, [])  # missing subcommand must NOT error
    assert sub_action.required is True  # restored
