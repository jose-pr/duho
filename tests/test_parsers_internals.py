"""Direct unit tests for duho.parsers helpers.

Covers the helper functions and the per-instance surgery invariants that
``prerun_parse`` relies on:

* ``pop_action`` / ``insert_action`` / ``add_help_argument`` exercised directly;
* the surgery never mutates ``argparse``'s own classes -- proven on both the
  clean-success path and an EXCEPTION path forced mid-parse -- and every
  per-instance ``__class__`` swap is undone either way.
"""

import argparse

import pytest

from duho.parsers import (
    _existing_subparsers,
    _restore_subparsers,
    _strip_subparsers,
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


def test_insert_action_default_appends_and_restores_help_visibility():
    """C038: the old default (``index=-1``) used ``list.insert(-1, x)``
    semantics -- inserting BEFORE the last action, not at the end -- and
    never re-added the action to its group, so a popped-then-reinserted flag
    parsed fine but vanished from ``--help``."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", help="a")
    parser.add_argument("--b", help="b")
    parser.add_argument("--c", help="c")
    action = pop_action(parser, "a")

    insert_action(parser, action)

    dests = [a.dest for a in parser._actions if a.dest != "help"]
    assert dests == ["b", "c", "a"]  # appended at the end, not before "c"
    help_text = parser.format_help()
    assert "--a" in help_text  # visible again in the OPTIONS section
    ns = parser.parse_args(["--a", "1"])
    assert ns.a == "1"


def test_pop_action_removes_from_mutually_exclusive_group():
    """C038: popping a member of a REQUIRED mutex group left it in
    ``_mutually_exclusive_groups``, so the group's own error kept naming a
    flag that no longer existed."""
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--x")
    group.add_argument("--y")

    pop_action(parser, "x")

    assert not any(
        a.dest == "x"
        for g in parser._mutually_exclusive_groups
        for a in g._group_actions
    )
    with pytest.raises(SystemExit):
        parser.parse_args([])  # "one of the arguments --y is required"
    message = parser.format_usage()
    assert "--x" not in message


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
    assert not hasattr(action, "_duho_action_called_")
    assert not hasattr(action, "_duho_disable_depth_")


def test_disable_subparser_check_is_reentrant():
    """C034: a NESTED disable used to save the already-relaxed state, so the
    inner ``enable`` restored THAT (still relaxed) and the outer ``enable``
    found nothing left to restore -- the action stayed permanently relaxed. A
    depth counter now means only the OUTERMOST pair actually saves/restores.
    """
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("go")
    action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    original_cls = type(action)

    disable_subparser_check(action)
    disable_subparser_check(action)  # nested
    assert type(action) is not original_cls

    enable_subparser_check(action)  # matches the INNER disable only
    assert type(action) is not original_cls  # still relaxed -- outer pair open
    assert action.choices is None

    enable_subparser_check(action)  # matches the OUTER disable
    assert type(action) is original_cls
    assert not hasattr(action, "_duho_saved_")
    assert not hasattr(action, "_duho_disable_depth_")

    # A real parse afterward rejects an unknown subcommand again -- proof the
    # check was genuinely restored, not just the class swapped back.
    with pytest.raises(SystemExit):
        parser.parse_args(["bogus"])


# --------------------------------------------------------------------------
# C044: the relaxed subparsers action must not leak a "==SUPPRESS==" key
# --------------------------------------------------------------------------


def test_relaxed_subparsers_action_skips_suppressed_dest():
    """A plain ``add_subparsers()`` with no explicit ``dest=`` defaults to
    ``argparse.SUPPRESS``. argparse's own ``_SubParsersAction.__call__``
    guards ``setattr`` with ``self.dest is not SUPPRESS``; the relaxed
    replacement used to skip that guard, leaving a literal ``"==SUPPRESS=="``
    key on the returned namespace."""
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers()  # no dest= -> SUPPRESS
    subs.add_parser("go")
    action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    assert action.dest is argparse.SUPPRESS

    try:
        disable_subparser_check(action)
        namespace = argparse.Namespace()
        action(parser, namespace, ["go"])
    finally:
        enable_subparser_check(action)

    assert "==SUPPRESS==" not in vars(namespace)


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
    # The subparsers action was DETACHED (never class-swapped) by
    # prerun_parse's new subparser-free design -- no relaxed-subclass
    # bookkeeping to leak either way.
    assert not hasattr(sub_action, "_duho_saved_")
    assert not hasattr(sub_action, "_duho_action_called_")
    # And it's back in the parser's own action list despite the exception.
    assert sub_action in parser._actions


def test_strip_and_restore_subparsers_removes_from_the_actual_group_list():
    """A073: a naive removal (matching what ``parse_globals`` used to do by
    hand) tried ``parser._subparsers._actions.remove(action)`` -- but that IS
    the SAME list object as ``parser._actions`` (already emptied by the first
    removal), so the second removal was always a dead branch and the action
    stayed in its OWN group's ``_group_actions`` (what ``format_help`` walks).
    ``_strip_subparsers`` removes it from the real owning group instead, and
    ``_restore_subparsers`` puts it back in the exact same spot."""
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("go")
    action = _existing_subparsers(parser)
    assert action is not None
    owning_group = next(g for g in parser._action_groups if action in g._group_actions)
    group_index = owning_group._group_actions.index(action)

    saved = _strip_subparsers(parser)
    assert action not in parser._actions
    assert action not in owning_group._group_actions
    assert "{go}" not in parser.format_help()

    _restore_subparsers(parser, saved)
    assert action in parser._actions
    assert owning_group._group_actions[group_index] is action
    assert "{go}" in parser.format_help()


def test_strip_subparsers_is_a_noop_without_one():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flag")
    assert _strip_subparsers(parser) is None
    _restore_subparsers(parser, None)  # must not raise
    assert [a.dest for a in parser._actions if a.dest != "help"] == ["flag"]


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


# --------------------------------------------------------------------------
# prerun_parse on a clean (non-exception) run
# --------------------------------------------------------------------------


def test_prerun_parse_leaves_argparse_classes_untouched_on_success():
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


def test_prerun_parse_sequential_calls_do_not_interfere():
    """Two prerun_parse calls on the SAME parser see independent results."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--flag")
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("go")
    a = prerun_parse(parser, ["--flag", "1", "go"])
    b = prerun_parse(parser, ["--flag", "2", "go"])
    assert a.flag == "1"
    assert b.flag == "2"


def test_pop_action_removes_flag_from_format_help_but_keeps_others():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gone", help="should disappear")
    parser.add_argument("--kept", help="stays")
    pop_action(parser, "gone")
    help_text = parser.format_help()
    assert "--gone" not in help_text
    assert "--kept" in help_text
