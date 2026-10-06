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


def _long_flag_or_first(builder: "_ArgumentBuilder") -> "tuple[str, bool]":
    """The flag to encode a value under, and whether it is a long flag.

    Prefers the first declared ``--long`` flag (a value can then always be
    attached with ``=``, which argparse never reinterprets); falls back to
    the field's sole flag for a short-flag-only field.
    """
    for flag in builder.flags:
        if flag.startswith("--"):
            return flag, True
    return builder.flags[0], False


def _looks_like_negative_number(token: str, parser: "_argparse.ArgumentParser") -> bool:
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
    parser: "_argparse.ArgumentParser",
    *,
    forbidden: "frozenset" = frozenset(),
) -> None:
    """Refuse a positional token argparse would parse as an option or as the
    ``--`` passthrough separator, rather than silently mis-parsing it or
    letting it leak into ``_passthrough_``. Also refuses a token equal to a
    name in ``forbidden`` -- ``call_tool`` passes the union of THIS level's
    own subcommand names (see :func:`_sibling_names`) and every ANCESTOR
    level's own subcommand names/aliases, so a client cannot set an
    ancestor's own optional/variadic positional field to a value that would
    read as a sibling selector -- AT THAT LEVEL OR ANY SHALLOWER ONE -- once
    appended ahead of it (a security-relevant guard: MCP tool arguments are
    LLM-controlled). Raises :class:`InvalidArgumentsError` rather than a bare
    ``ValueError``: a value that cannot be safely encoded as argv at all is a
    malformed REQUEST, not a command that ran and failed, so ``call_tool``
    lets it propagate as a JSON-RPC error instead of mapping it to a tool
    result's ``isError: true`` (see the module docstring's "Malformed
    requests" note)."""
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
    argv: "list[str]",
    flag: str,
    is_long: bool,
    token: str,
    parser: "_argparse.ArgumentParser",
) -> None:
    """Append one option occurrence for ``token``: a long flag is
    always attached with ``=`` so argparse never reinterprets the value; a
    short-flag-only field refuses a value that looks like another option
    (there is no safe attached form for a short flag) -- also an
    :class:`InvalidArgumentsError`, the same request-level classification as
    :func:`_reject_unsafe_positional`. The literal value ``"--"`` is attached
    like any other, but only when ``parser`` keeps it (every parser duho
    builds does); an unpatched parser could silently drop it, so it is refused."""
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


def _sibling_names(parser: "_argparse.ArgumentParser") -> "frozenset":
    """Every subcommand name (canonical + alias) registered DIRECTLY on
    ``parser`` -- empty when it has no subparsers action at all. Used to
    refuse a positional value that collides with one of THIS level's own
    choices, or (unioned with every ancestor's own call to this same
    function) an ANCESTOR's own choices -- see :func:`_reject_unsafe_positional`
    and ``call_tool``'s accumulation of ``ancestor_forbidden`` as it walks
    the chain. Scoped to one parser at a time, never the whole tree, so a
    same-named command living elsewhere (a different, unrelated node
    entirely) never triggers it by coincidence. A command excluded from the
    tool surface is left out, so a client cannot confirm its name by being
    refused; the dispatch-identity guard still stops a shifted dispatch."""
    action = _parsers.find_subparsers(parser)
    if action is None:
        return frozenset()
    return frozenset(action.choices or ()) - _hidden_choice_names(parser)


def _dest_action(
    parser: "_argparse.ArgumentParser", dest: str
) -> "_ty.Optional[_argparse.Action]":
    """The already-built ``argparse.Action`` registered for ``dest`` on
    ``parser``, or ``None``. Reading the REAL parser (built once by
    ``cls._parser_()`` + ``_apply_layers``, see :func:`_tree_for`) is what
    lets :func:`_bool_action_kind` tell a plain ``store_true`` apart from a
    layered field's ``BooleanOptionalAction`` -- recomputing the action from
    ``builder._kwargs()`` alone (with no ``layered=`` argument) silently
    disagreed with what got built whenever the field is env/config-layered
    (``Args._parser_()`` threads ``layered=True`` through at build time; a
    bare ``_kwargs()`` call defaults it to ``False``).
    """
    for action in parser._actions:
        if action.dest == dest:
            return action
    return None


#: `type(action).__name__` -> the bool-flag "kind" `_synthesize_argv` needs,
#: for the two argparse action classes with no public name of their own
#: (`argparse.BooleanOptionalAction` IS public and checked separately via
#: `isinstance`). Both class names have been stable, documented-by-behavior
#: argparse internals for the module's whole history.
_BOOL_ACTION_KINDS = {
    "_StoreTrueAction": "store_true",
    "_StoreFalseAction": "store_false",
}


def _bool_action_kind(action: "_ty.Optional[_argparse.Action]") -> "_ty.Optional[str]":
    """Classify ``action`` as ``"store_true"``/``"store_false"``/
    ``"boolean_optional"``, or ``None`` for anything else (including
    ``None`` itself, or an explicit non-bool ``action=`` override that
    happens to sit on a ``bool``-typed field, e.g. ``store_const``) -- the
    caller falls through to its OWN, unrelated handling for that case."""
    if action is None:
        return None
    if isinstance(action, _argparse.BooleanOptionalAction):
        return "boolean_optional"
    return _BOOL_ACTION_KINDS.get(type(action).__name__)


def _pinned_default(
    cls: type, builder: "_ArgumentBuilder", parser: "_argparse.ArgumentParser"
) -> "_ty.Optional[str]":
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
    arguments: "dict",
    parser: "_argparse.ArgumentParser",
    *,
    skip: "_ty.Optional[frozenset]" = None,
    ancestor_forbidden: "frozenset" = frozenset(),
    pin_positionals: bool = False,
) -> "list[str]":
    """Turn a JSON ``arguments`` object into argv for ``cls``'s OWN fields.

    Iterates ``cls._getargs_()`` in declaration order. A field named in
    ``skip`` contributes nothing at all -- ``call_tool`` passes the set of
    field names that are ALSO declared by a DEEPER ancestor in the current
    dispatch chain, so a name redeclared at multiple levels only ever binds
    at the deepest one (its own schema, per :func:`_input_schema_for_node`,
    already only ever describes that same deepest declaration); omit it
    (the default) for a standalone, single-level call. A field absent from
    ``arguments``, or explicitly ``null``, ALSO contributes nothing (JSON
    ``null`` means "not supplied", never the literal string ``"None"``).
    Branches on the field's EFFECTIVE ``argparse`` action, not a re-derived
    guess, so this never drifts from what ``add_to_parser`` itself would
    register:

    * a bare bool flag -- resolved from the REAL action already built on
      ``parser`` (see :func:`_bool_action_kind`), since a re-derived guess
      can disagree for an env/config-LAYERED field: ``store_true`` -> the
      bare flag when ``True``, nothing when ``False`` (there is no CLI
      spelling for ``False`` here, matching the plain CLI's own limit);
      ``store_false`` -> the bare flag when ``False``, nothing when ``True``;
      ``BooleanOptionalAction`` -> the bare flag when ``True``, ``--no-<x>``
      when ``False`` (raising if the field has no long flag to negate) --
      this is what lets an env-layered bool be turned back to ``False``.
    * a counting flag (``-v``/``-q`` style) -> a single bundled short token
      (``-vvv``) for a short-flag-only field, else the long flag repeated
      ``value`` times; capped by ``_MAX_COUNT_VALUE`` at the schema/
      validation layer (:func:`json_schema_for_field`/`_validate_arguments`),
      not here.
    * ``store_const``/``append_const`` -> the bare flag when ``value`` is truthy.
    * a ``nargs="?"`` OPTION given an actual JSON boolean -> the bare flag when
      ``True`` (the option's own ``const``), nothing when ``False``.
    * a ``dict`` field backed by duho's own generic ``KEY=VALUE`` factory
      (:class:`duho._fieldspec._KVFactory`) -> one such token per item,
      repeating the flag. A dict field with a DIFFERENT, custom whole-string
      ``type=`` override (duho's only one is ``LoggingArgs.loglevels``'s
      ``parse_loglevels``, parsing its own ``NAME:LEVEL[,NAME:LEVEL...]``
      grammar from a single token) -> all items joined into ONE such token
      instead -- emitting the generic ``KEY=VALUE`` form here fed a value
      like ``synapp=10`` straight into that grammar and always failed.
    * a ``list``/``set``/``tuple`` field -> one token per element, repeating
      the flag (a positional repeats bare tokens with no flag).
    * anything else (str/int/float/``Literal[True, False]``/Enum/Path/a custom
      ``action=``/``type=`` with no registered override) -> ``str(value)``.

    Every option value is emitted as a single attached ``--flag=value`` token
    (never ``[flag, value]``), so a value starting with ``-`` can never be
    reinterpreted as a different flag; a positional value that would be
    parsed as an option (or the ``--`` passthrough separator), or that
    collides with one of THIS level's own subcommand names OR one named in
    ``ancestor_forbidden`` (security-relevant: MCP tool arguments are
    LLM-controlled -- ``call_tool`` passes every ANCESTOR level's own
    subcommand names/aliases here, since such a value could otherwise be
    swallowed by an ancestor's own optional/variadic positional and
    reinterpreted as ITS subcommand selector once the literal name tokens
    shift -- see :func:`_reject_unsafe_positional`), is refused outright.

    ``pin_positionals`` (set for every level that is followed by a deeper
    subcommand name) emits the default of an omitted optional positional
    explicitly: argparse otherwise lets that positional take the next
    subcommand name and reads the token after it as the subcommand.
    """
    argv: "list[str]" = []
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
                # No matching action found on the parser (should not happen
                # for a bare bool flag) -- fall back to the old heuristic.
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
                # A malformed-request problem, not a broken command -- see
                # `_reject_unsafe_positional`'s docstring for the same
                # classification. `_validate_arguments` also enforces the
                # published `minimum: 0` before dispatch ever reaches here;
                # this is the defense-in-depth fallback.
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
    step: "_Node",
    arguments: "dict",
    *,
    skip: "_ty.Optional[frozenset]" = None,
    ancestor_forbidden: "frozenset" = frozenset(),
) -> "list[str]":
    """:func:`_synthesize_argv`'s counterpart for a bare module command --
    one with no declared ``Args`` (:func:`_effective_cls` is ``None``): maps
    ``arguments`` onto ``step.parser``'s own actions (:func:`_own_dests`)
    directly, with no ``ArgumentBuilder`` behind any of them. Only ever
    called for ``step is node`` itself (a module command is always a leaf).

    Deliberately simpler than :func:`_synthesize_argv` -- there is no
    ``ArgumentBuilder``/``NS(...)`` metadata to consult here, only the
    action's own ``nargs``/``type``/class -- but applies the SAME
    request-level safety checks (:func:`_reject_unsafe_positional`/
    :func:`_emit_option`) for every emitted token.
    """
    argv: "list[str]" = []
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
    return argv


def _synthesize_step_argv(
    step: "_Node",
    arguments: "dict",
    *,
    skip: "frozenset",
    ancestor_forbidden: "frozenset",
    pin_positionals: bool = False,
) -> "list[str]":
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
        # A module command's declared field that collided with an inherited
        # global at registration time was silently skipped -- never emit a
        # token for it here either (see `_input_schema_for_node`'s matching
        # skip).
        skip = skip | {b.name for b in eff_cls._getargs_() if b.name not in own}
    return _synthesize_argv(
        eff_cls,
        arguments,
        step.parser,
        skip=skip,
        ancestor_forbidden=ancestor_forbidden,
        pin_positionals=pin_positionals,
    )
