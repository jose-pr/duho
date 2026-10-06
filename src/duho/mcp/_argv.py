from __future__ import annotations

import argparse as _argparse
import typing as _ty
from pathlib import PurePath as _PurePath

from .. import parsers as _parsers
from ..args import ArgumentBuilder as _ArgumentBuilder
from ..args import _raw_config_values, _raw_env_values
from .._fieldspec import _KVFactory as _KVFactory

from ._errors import InvalidArgumentsError
from ._tree import _Node, _effective_cls, _hidden_choice_names, _own_dests

# --------------------------------------------------------------------------
# Step 3: argv synthesis + call_tool
# --------------------------------------------------------------------------


def _long_flag_or_first(builder: _ArgumentBuilder) -> tuple[str, bool]:
    """The flag to encode a value under, and whether it is a long flag.

    Prefers the first declared ``--long`` flag (a value can then always be
    attached with ``=``, which argparse never reinterprets); falls back to
    the field's sole flag for a short-flag-only field.
    """
    for flag in builder.flags:
        if flag.startswith("--"):
            return flag, True
    return builder.flags[0], False


def _looks_like_negative_number(token: str, parser: _argparse.ArgumentParser) -> bool:
    """True when ``token`` is safe as a positional because argparse's OWN
    negative-number exemption would accept it (mirrors
    ``ArgumentParser._negative_number_matcher``/``_has_negative_number_optionals``).
    """
    matcher = getattr(parser, "_negative_number_matcher", None)
    if matcher is None or not matcher.match(token):
        return False
    return not getattr(parser, "_has_negative_number_optionals", None)


def _reject_unsafe_positional(
    token: str,
    parser: _argparse.ArgumentParser,
    *,
    forbidden: frozenset = frozenset(),
) -> None:
    """Refuse a positional token argparse would parse as an option or as the
    ``--`` separator, or one equal to a name in ``forbidden``.

    ``forbidden`` holds this level's and every ancestor level's subcommand
    names, so a client-supplied value (LLM-controlled) cannot read as a
    subcommand selector once appended. Raises :class:`InvalidArgumentsError`,
    a malformed request that ``call_tool`` surfaces as a JSON-RPC error rather
    than a tool result with ``isError``.
    """
    if token == "--" or (
        token.startswith("-")
        and token != "-"
        and not _looks_like_negative_number(token, parser)
    ):
        raise InvalidArgumentsError(
            "value %r cannot be passed as a positional argument: it would "
            "be parsed as an option (or the '--' passthrough separator)" % (token,)
        )
    if token in forbidden:
        raise InvalidArgumentsError(
            "value %r cannot be passed as a positional argument at this "
            "level: it collides with a subcommand name at this level or an "
            "ancestor level" % (token,)
        )


def _emit_option(
    argv: list[str],
    flag: str,
    is_long: bool,
    token: str,
    parser: _argparse.ArgumentParser,
) -> None:
    """Append one option occurrence for ``token``.

    A long flag is attached with ``=``; a short-flag-only field refuses a value
    that looks like another option (no safe attached form), raising
    :class:`InvalidArgumentsError`. ``"--"`` is refused unless ``parser`` keeps
    it (every duho-built parser does).
    """
    if token == "--" and not getattr(parser, "_duho_keeps_double_dash_", False):
        raise InvalidArgumentsError(
            "value '--' cannot be passed to %s: its parser was not built by "
            "duho and may drop an attached '--' value" % (flag,)
        )
    if is_long:
        argv.append("%s=%s" % (flag, token))
        return
    if token.startswith("-") and token != "-":
        raise InvalidArgumentsError(
            "value %r cannot be passed to %s: it has no long form to attach "
            "the value to safely" % (token, flag)
        )
    argv.extend([flag, token])


def _sibling_names(parser: _argparse.ArgumentParser) -> frozenset:
    """Subcommand names and aliases registered directly on ``parser``.

    Scoped to one parser, never the tree. A command excluded from the tool
    surface is left out so a client cannot confirm its name by being refused;
    the dispatch-identity guard still stops a shifted dispatch.
    """
    action = _parsers.find_subparsers(parser)
    if action is None:
        return frozenset()
    return frozenset(action.choices or ()) - _hidden_choice_names(parser)


def _dest_action(
    parser: _argparse.ArgumentParser, dest: str
) -> _ty.Optional[_argparse.Action]:
    """The already-built action for ``dest`` on ``parser``, or ``None``.

    Read from the real parser: ``builder._kwargs()`` alone defaults
    ``layered`` to ``False`` and disagrees with the built action for an
    env/config-layered bool field.
    """
    for action in parser._actions:
        if action.dest == dest:
            return action
    return None


#: Bool-flag kind by `type(action).__name__`, for the two argparse action
#: classes with no public name (`BooleanOptionalAction` is checked by `isinstance`).
_BOOL_ACTION_KINDS = {
    "_StoreTrueAction": "store_true",
    "_StoreFalseAction": "store_false",
}


def _bool_action_kind(action: _ty.Optional[_argparse.Action]) -> _ty.Optional[str]:
    """``"store_true"``, ``"store_false"``, ``"boolean_optional"``, or ``None``
    for any other action (including a non-bool ``action=`` override)."""
    if action is None:
        return None
    if isinstance(action, _argparse.BooleanOptionalAction):
        return "boolean_optional"
    return _BOOL_ACTION_KINDS.get(type(action).__name__)


def _pinned_default(
    cls: type, builder: _ArgumentBuilder, parser: _argparse.ArgumentParser
) -> _ty.Optional[str]:
    """The argv token that spells an optional single-value positional's own
    default, or ``None`` when it has no plain default to spell or a value can
    come from the environment or a config file (which must keep winning)."""
    action = _dest_action(parser, builder.name)
    if action is None or action.nargs != "?":
        return None
    default = action.default
    if isinstance(default, bool) or not isinstance(
        default, (str, int, float, _PurePath)
    ):
        return None
    config_table = getattr(parser, "_duho_raw_config_table_", None) or {}
    if builder.name in _raw_env_values(cls) or builder.name in _raw_config_values(
        cls, config_table
    ):
        return None
    return str(default)


def _synthesize_argv(
    cls: type,
    arguments: dict,
    parser: _argparse.ArgumentParser,
    *,
    skip: _ty.Optional[frozenset] = None,
    ancestor_forbidden: frozenset = frozenset(),
    pin_positionals: bool = False,
) -> list[str]:
    """Turn a JSON ``arguments`` object into argv for ``cls``'s own fields.

    Fields in ``skip`` (redeclared deeper in the chain), absent or ``null``
    contribute nothing. Branches on the action built on ``parser``, so layered
    bools match; a dict with a custom ``type=`` (``parse_loglevels``) is ONE
    ``NAME:LEVEL,...`` token. Option values are attached as ``--flag=value``.
    A positional that reads as an option, ``--`` or a subcommand name (this
    level's or in ``ancestor_forbidden``) is refused: values are LLM-controlled.
    ``pin_positionals`` emits an omitted optional positional's default so it
    cannot swallow the next subcommand name.
    """
    argv: list[str] = []
    forbidden = _sibling_names(parser) | ancestor_forbidden
    for builder in cls._getargs_():
        name = builder.name
        if skip is not None and name in skip:
            continue
        if pin_positionals and builder.is_positional:
            pinned = _pinned_default(cls, builder, parser)
            if pinned is not None and arguments.get(name) is None:
                argv.append(pinned)
                continue
        if name not in arguments:
            continue
        value = arguments[name]
        if value is None:
            continue

        is_positional = builder.is_positional
        flag = is_long = None
        if not is_positional:
            flag, is_long = _long_flag_or_first(builder)
        action = builder._kwargs().get("action")

        if builder.type is bool and builder.choices is None:
            kind = _bool_action_kind(_dest_action(parser, name))
            if kind is None and builder.is_bare_bool_flag:
                # No action found on the parser: infer it from the default.
                kind = "boolean_optional" if builder.default is True else "store_true"
            if kind is not None:
                if kind == "boolean_optional":
                    if value:
                        argv.append(flag)
                    elif not is_long:
                        raise ValueError(
                            "field %r has no long flag to negate; false "
                            "cannot be expressed over MCP" % (name,)
                        )
                    else:
                        argv.append("--no-" + flag[2:])
                elif kind == "store_false":
                    if not value:
                        argv.append(flag)
                else:  # store_true
                    if value:
                        argv.append(flag)
                continue

        if action == "count":
            count = value if isinstance(value, int) else int(value)
            if count < 0:
                # A malformed request; `_validate_arguments` already enforces
                # `minimum: 0`, so this is a fallback.
                raise InvalidArgumentsError(
                    "field %r (a counting flag) cannot be negative" % (name,)
                )
            if is_long or not count:
                argv.extend([flag] * count)
            else:
                # Bundle a short counting flag into one token (`-vvv`)
                # instead of `count` separate ones.
                argv.append("-" + flag[1:] * count)
            continue

        if action in ("store_const", "append_const"):
            if value:
                argv.append(flag)
            continue

        if builder.nargs == "?" and not is_positional and isinstance(value, bool):
            if value:
                argv.append(flag)
            continue

        if builder.collection is dict:
            if not isinstance(value, dict):
                raise ValueError("field %r expects a JSON object" % (name,))
            if isinstance(builder.type, _KVFactory):
                tokens = ["%s=%s" % (key, val) for key, val in value.items()]
            else:
                tokens = [",".join("%s:%s" % (key, val) for key, val in value.items())]
                if not tokens[0]:
                    tokens = []
            for token in tokens:
                if is_positional:
                    _reject_unsafe_positional(token, parser, forbidden=forbidden)
                    argv.append(token)
                else:
                    _emit_option(argv, flag, is_long, token, parser)
            continue

        if builder.collection in (list, set, tuple):
            if not isinstance(value, (list, tuple, set)):
                raise ValueError("field %r expects a JSON array" % (name,))
            for item in value:
                token = str(item)
                if is_positional:
                    _reject_unsafe_positional(token, parser, forbidden=forbidden)
                    argv.append(token)
                else:
                    _emit_option(argv, flag, is_long, token, parser)
            continue

        token = str(value)
        if is_positional:
            _reject_unsafe_positional(token, parser, forbidden=forbidden)
            argv.append(token)
        else:
            _emit_option(argv, flag, is_long, token, parser)
    return argv


def _synthesize_argv_from_actions(
    step: _Node,
    arguments: dict,
    *,
    skip: _ty.Optional[frozenset] = None,
    ancestor_forbidden: frozenset = frozenset(),
) -> list[str]:
    """:func:`_synthesize_argv` for a bare module command (no declared ``Args``).

    Maps ``arguments`` onto the parser's own actions, with no
    ``ArgumentBuilder`` to consult, but applies the same safety checks to every
    token. Only called for a leaf.
    """
    argv: list[str] = []
    subparser_choice: _ty.Optional[str] = None
    parser = step.parser
    forbidden = _sibling_names(parser) | ancestor_forbidden
    own = _own_dests(parser) or set()
    for action in parser._actions:
        dest = action.dest
        if dest not in own:
            continue
        if skip is not None and dest in skip:
            continue
        if dest not in arguments:
            continue
        value = arguments[dest]
        if value is None:
            continue

        if isinstance(action, _argparse._SubParsersAction):
            # Checked against the hook's own subparser names; goes last,
            # since everything after it belongs to that subparser.
            if not isinstance(value, str) or value not in action.choices:
                raise InvalidArgumentsError(
                    "value %r is not one of %s for %r"
                    % (value, ", ".join(sorted(action.choices)), dest)
                )
            subparser_choice = value
            continue

        is_positional = not action.option_strings
        flag = action.option_strings[0] if action.option_strings else None
        is_long = bool(flag) and flag.startswith("--")

        if action.nargs == 0:
            # A bare 0-arg action over MCP is a JSON boolean; `store_false`
            # is the only 0-arg action whose "on" state is FALSE.
            truthy = (
                not value if isinstance(action, _argparse._StoreFalseAction) else value
            )
            if truthy:
                argv.append(flag)
            continue

        if isinstance(action, _argparse._AppendAction) or action.nargs in ("*", "+"):
            items = value if isinstance(value, (list, tuple)) else [value]
            for item in items:
                token = str(item)
                if is_positional:
                    _reject_unsafe_positional(token, parser, forbidden=forbidden)
                    argv.append(token)
                else:
                    _emit_option(argv, flag, is_long, token, parser)
            continue

        token = str(value)
        if is_positional:
            _reject_unsafe_positional(token, parser, forbidden=forbidden)
            argv.append(token)
        else:
            _emit_option(argv, flag, is_long, token, parser)
    if subparser_choice is not None:
        argv.append(subparser_choice)
    return argv


def _synthesize_step_argv(
    step: _Node,
    arguments: dict,
    *,
    skip: frozenset,
    ancestor_forbidden: frozenset,
    pin_positionals: bool = False,
) -> list[str]:
    """One chain step's own argv contribution, dispatching to
    :func:`_synthesize_argv` (a real declared class -- a class command, or a
    module command with its own ``Args``) or :func:`_synthesize_argv_from_actions`
    (a bare module command) depending on :func:`_effective_cls`.
    """
    eff_cls = _effective_cls(step)
    if eff_cls is None:
        return _synthesize_argv_from_actions(
            step, arguments, skip=skip, ancestor_forbidden=ancestor_forbidden
        )
    own = _own_dests(step.parser)
    if own is not None:
        # A declared field skipped at registration (it collided with an
        # inherited global) never gets a token; see `_input_schema_for_node`.
        skip = skip | {b.name for b in eff_cls._getargs_() if b.name not in own}
    return _synthesize_argv(
        eff_cls,
        arguments,
        step.parser,
        skip=skip,
        ancestor_forbidden=ancestor_forbidden,
        pin_positionals=pin_positionals,
    )
