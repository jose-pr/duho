"""Subparser utilities and helper functions."""

import argparse as _argparse
import typing as _ty


def pop_action(parser: _argparse.ArgumentParser, name: str):
    """Remove an action from a parser by destination name.

    Removes the action from the parser's action list, its option-string map, AND
    the owning argument group's ``_group_actions`` -- ``format_help`` renders from
    the group lists, so missing the last one left a "removed" flag still visible
    in help output (M20).
    """
    index = None
    for idx, action in enumerate(parser._actions):
        if action.dest == name:
            index = idx
            break
    if index is not None:
        action = parser._actions.pop(index)
        for k in action.option_strings:
            parser._option_string_actions.pop(k)
        container = getattr(action, "container", None)
        group_actions = getattr(container, "_group_actions", None)
        if group_actions is not None and action in group_actions:
            group_actions.remove(action)
        return action
    raise KeyError(name)


def insert_action(
    parser: _argparse.ArgumentParser, action: _argparse.Action, index: int = -1
):
    """Insert an action into a parser at a given index."""
    parser._actions.insert(index, action)
    for k in action.option_strings:
        parser._option_string_actions[k] = action


def add_help_argument(parser: _argparse.ArgumentParser):
    """Add a help argument to a parser."""
    return parser.add_argument(
        "-h",
        "--help",
        action="help",
        default=_argparse.SUPPRESS,
        help=("show this help message and exit"),
    )


class _NoOpHelpAction(_argparse._HelpAction):
    """A ``_HelpAction`` whose ``__call__`` does nothing.

    Used only transiently via a per-instance ``__class__`` swap so a ``--help`` in
    the globals prepass does not print help and exit. Swapping the *instance's*
    class (not ``_argparse._HelpAction.__call__``) keeps the surgery local and
    thread-safe -- argparse's own classes are never mutated (M1).
    """

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: D401
        return None


class _RelaxedSubParsersAction(_argparse._SubParsersAction):
    """A ``_SubParsersAction`` whose ``__call__`` tolerates any subcommand name.

    Records the first subcommand name, then re-parses the remaining args with the
    parent parser so trailing globals still land; unknown/incomplete subcommands
    do not error. Installed transiently via a per-instance ``__class__`` swap
    (never a class-global patch of argparse) so it is thread-safe and reentrant.
    The "seen a subcommand yet" flag lives on the instance, not a shared closure.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        parser_name = values[0]
        arg_strings = values[1:]

        if not getattr(self, "_duho_action_called", False):
            setattr(namespace, self.dest, parser_name)
            self._duho_action_called = True  # type: ignore[attr-defined]

        subnamespace, arg_strings = parser.parse_known_args(arg_strings, namespace)
        for key, value in vars(subnamespace).items():
            setattr(namespace, key, value)

        if arg_strings:
            vars(namespace).setdefault(_argparse._UNRECOGNIZED_ARGS_ATTR, [])
            getattr(namespace, _argparse._UNRECOGNIZED_ARGS_ATTR).extend(arg_strings)


def disable_subparser_check(action: _argparse._SubParsersAction):
    """Relax name validation on THIS ``_SubParsersAction`` instance.

    Per-instance surgery: swaps only this action's class to
    :class:`_RelaxedSubParsersAction` and nulls its ``choices`` (so argparse's
    name check is skipped), saving both for :func:`enable_subparser_check` to
    restore. No ``argparse`` class attribute is mutated (M1).
    """
    action._duho_saved_ = (action.__class__, action.choices)  # type: ignore[attr-defined]
    action.choices = None  # type:ignore
    action._duho_action_called = False  # type: ignore[attr-defined]
    action.__class__ = _RelaxedSubParsersAction


def enable_subparser_check(action: _argparse._SubParsersAction):
    """Restore the class + choices saved by :func:`disable_subparser_check`."""
    saved = getattr(action, "_duho_saved_", None)
    if saved is not None:
        action.__class__, action.choices = saved
        del action._duho_saved_
    if hasattr(action, "_duho_action_called"):
        del action._duho_action_called


def _reachable_help_actions(
    parser: _argparse.ArgumentParser,
) -> "list[_argparse.Action]":
    """Collect every ``_HelpAction`` on ``parser`` and its subparser tree."""
    seen: "set[int]" = set()
    result: "list[_argparse.Action]" = []
    stack = [parser]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for action in current._actions:
            if isinstance(action, _argparse._HelpAction):
                result.append(action)
            elif isinstance(action, _argparse._SubParsersAction):
                for sub in (action.choices or {}).values():
                    stack.append(sub)
    return result


def prerun_parse(
    parser: _argparse.ArgumentParser, argv: "_ty.Sequence[str] | None" = None
):
    """Parse arguments without triggering help or strict subparser validation.

    All surgery is per-instance and restored in ``finally``: every reachable
    ``_HelpAction`` has its instance class swapped to a no-op, and the subparsers
    action is relaxed via :func:`disable_subparser_check`. argparse's own classes
    are never mutated, so this is thread-safe and reentrant (M1).
    """
    subparser = None
    if parser._subparsers:
        for action in parser._subparsers._actions:
            if isinstance(action, _argparse._SubParsersAction):
                subparser = action

    help_actions = _reachable_help_actions(parser)
    saved_help = [(a, a.__class__) for a in help_actions]
    for a in help_actions:
        a.__class__ = _NoOpHelpAction

    required = None
    try:
        if subparser:
            required = subparser.required
            subparser.required = False
            disable_subparser_check(subparser)
        args, _ = parser.parse_known_args(argv)
    finally:
        if subparser:
            enable_subparser_check(subparser)
            subparser.required = required  # type: ignore
        for a, cls in saved_help:
            a.__class__ = cls
    return args


__all__ = [
    "pop_action",
    "insert_action",
    "add_help_argument",
    "disable_subparser_check",
    "enable_subparser_check",
    "prerun_parse",
]
