"""Subparser utilities and helper functions."""

import argparse as _argparse
import typing as _ty


def pop_action(parser: _argparse.ArgumentParser, name: str):
    """Remove an action from a parser by destination name."""
    index = None
    for idx, action in enumerate(parser._actions):
        if action.dest == name:
            index = idx
            break
    if index is not None:
        action = parser._actions.pop(index)
        for k in action.option_strings:
            parser._option_string_actions.pop(k)
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


_SUBPARSERACTION_CALL = _argparse._SubParsersAction.__call__


def disable_subparser_check(action: _argparse._SubParsersAction):
    """Disable validation of subparser names (allow any subparser to be invoked)."""
    action.choices = None  # type:ignore
    action_called = False

    def action_call(
        self: _argparse._SubParsersAction,
        parser: _argparse.ArgumentParser,
        namespace: _argparse.Namespace,
        values,
        option_string=None,
    ):
        nonlocal action_called
        parser_name = values[0]
        arg_strings = values[1:]

        if not action_called:
            setattr(namespace, self.dest, parser_name)
            action_called = True

        subnamespace, arg_strings = parser.parse_known_args(arg_strings, namespace)
        for key, value in vars(subnamespace).items():
            setattr(namespace, key, value)

        if arg_strings:
            vars(namespace).setdefault(_argparse._UNRECOGNIZED_ARGS_ATTR, [])
            getattr(namespace, _argparse._UNRECOGNIZED_ARGS_ATTR).extend(arg_strings)

    _argparse._SubParsersAction.__call__ = action_call


def enable_subparser_check(action: _argparse._SubParsersAction):
    """Re-enable validation of subparser names."""
    _argparse._SubParsersAction.__call__ = _SUBPARSERACTION_CALL
    action.choices = action._name_parser_map


_HELPACTION_CALL = _argparse._HelpAction.__call__


def prerun_parse(
    parser: _argparse.ArgumentParser, argv: "_ty.Sequence[str] | None" = None
):
    """Parse arguments without triggering help or strict subparser validation."""
    subparser = None
    if parser._subparsers:
        for action in parser._subparsers._actions:
            if isinstance(action, _argparse._SubParsersAction):
                subparser = action
    required = None
    _argparse._HelpAction.__call__ = lambda *args: ...  # type: ignore
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
        _argparse._HelpAction.__call__ = _HELPACTION_CALL
    return args


__all__ = [
    "pop_action",
    "insert_action",
    "add_help_argument",
    "disable_subparser_check",
    "enable_subparser_check",
    "prerun_parse",
]
