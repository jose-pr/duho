from __future__ import annotations

import argparse as _argparse
import copy as _copy
import typing as _ty

from ._meta import NOT_DEFINED

#: `nargs` values that make a positional variable-arity: the trigger set for
#: argparse's greedy positional-run matching (bpo-15112) when another positional
#: shares the parser.
_VARIADIC_NARGS = ("*", "+", "?")


def _has_variadic_positional(parser: _argparse.ArgumentParser) -> bool:
    """True if ``parser`` has at least one variable-arity positional.

    One is enough to need the reorder: a flag between it and another positional
    breaks greedy matching (bpo-15112), and a flag touching a lone variadic
    positional's run is swallowed as unrecognized (bpo-14191). The subparsers
    action (``_duho_command_``) is excluded, so a root with subcommands and no
    declared positional does not false-trigger.
    """
    positionals = [
        action
        for action in parser._actions  # type: ignore[attr-defined]
        if not action.option_strings
        and not isinstance(action, _argparse._SubParsersAction)  # type: ignore[attr-defined]
    ]
    return any(action.nargs in _VARIADIC_NARGS for action in positionals)


def _keep_attached_double_dash(parser: _argparse.ArgumentParser) -> None:
    """Make an option's attached ``--`` value (``--k=--``, ``-k--``) reach the field.

    Some argparse versions strip a bare ``--`` from every action's values, so an
    attached one is lost on those versions; this returns what newer argparse
    returns. A positional ``--`` is never touched. Idempotent per parser.
    """
    if getattr(parser, "_duho_keeps_double_dash_", False):
        return
    real_get_values = parser._get_values  # type: ignore[attr-defined]
    passthrough_nargs = (
        _argparse.PARSER,
        _argparse.REMAINDER,
        _argparse.SUPPRESS,
    )

    def _get_values(action, arg_strings):
        if (
            len(arg_strings) == 1
            and arg_strings[0] == "--"
            and action.option_strings
            and action.nargs not in passthrough_nargs
        ):
            value = parser._get_value(action, "--")  # type: ignore[attr-defined]
            parser._check_value(action, value)  # type: ignore[attr-defined]
            if action.nargs in (None, _argparse.OPTIONAL):
                return value
            return [value]
        return real_get_values(action, arg_strings)

    parser._get_values = _get_values  # type: ignore[attr-defined]
    parser._duho_keeps_double_dash_ = True  # type: ignore[attr-defined]


def _patch_parser_for_reorder(parser: _argparse.ArgumentParser) -> None:
    """Install only the flag-between-positionals reorder on a plain parser.

    For a module command's subparser (``runtime._register_module_command``),
    which never gets ``Args._initparser_``'s patched ``parse_known_args``: it
    needs no ``"#cls"`` dispatch or ``_passthrough_`` split, and there is no
    ``Args`` instance to build.
    """
    real_parse_known_args = parser.parse_known_args

    def parse_known_args(
        args: _ty.Sequence[str] | None = None,
        namespace: _argparse.Namespace | None = None,
    ):
        if args is not None and _has_variadic_positional(parser):
            args = _reorder_argv_for_variadic_positional(parser, list(args))
        return real_parse_known_args(args, namespace)

    parser.parse_known_args = parse_known_args  # type: ignore


def _short_cluster(known, token: str):
    """Return ``(action, self_contained)`` for a recognized short-option cluster, else None.

    Every character before the last must be a registered zero-value option; a
    character whose option takes exactly one value takes the rest of the token
    (``self_contained``) or, as the last character, the next argv token.
    """
    for position in range(1, len(token)):
        member = known.get("-" + token[position])
        if member is None:
            return None
        if member.nargs == 0:
            continue
        if member.nargs is not None:
            return None
        return member, position < len(token) - 1
    return known["-" + token[-1]], True


def _reorder_argv_for_variadic_positional(
    parser: _argparse.ArgumentParser, argv: list[str]
) -> list[str]:
    """Hoist recognized flags (and their values) before the positional run.

    Preprocessing only; it never decides an input is invalid. A token that the
    parser's ``_option_string_actions`` recognizes (exact, ``--flag=value``,
    attached short value ``-fVALUE``, or an unambiguous ``allow_abbrev`` prefix)
    moves with its values to a leading run, so ``flags + positionals`` keeps the
    positional run contiguous.

    Returns ``argv`` UNCHANGED on anything uncertain: an unrecognized ``-``
    token (not an accepted negative number) or a flag missing its value. A
    genuine typo then still gets argparse's own error from the real parse.
    """
    known = parser._option_string_actions  # type: ignore[attr-defined]
    negative_number_matcher = getattr(parser, "_negative_number_matcher", None)
    has_negative_number_optionals = bool(
        getattr(parser, "_has_negative_number_optionals", [])
    )
    allow_abbrev = getattr(parser, "allow_abbrev", True)

    subcommand_names = {
        name
        for action in parser._actions  # type: ignore[attr-defined]
        if isinstance(action, _argparse._SubParsersAction)  # type: ignore[attr-defined]
        for name in action.choices
    }

    flags: list[str] = []
    positionals: list[str] = []
    i = 0
    n = len(argv)
    while i < n:
        token = argv[i]
        if token in subcommand_names:
            # Everything after a subcommand name belongs to the subparser.
            positionals.extend(argv[i:])
            break
        if token == "--":
            # A literal `--`: `_passthrough_` already split on the FIRST one, so
            # this is a second, part of the payload. Stop reordering.
            positionals.extend(argv[i:])
            break

        action = known.get(token)
        self_contained = False
        key, eq, _ = token.partition("=")
        if action is None and eq:
            action = known.get(key)
            # `--flag=value` is a single self-contained token; no separate
            # value token to hoist alongside it.
            self_contained = action is not None
        if action is None and key.startswith("--") and allow_abbrev:
            # An unambiguous long-option PREFIX under `allow_abbrev` (`--filt`
            # for `--filter`): exactly one ACTION, aliases counting once, has an
            # option string starting with the flag half of the token.
            candidates = {
                id(a): a
                for opt, a in known.items()
                if opt.startswith("--") and opt.startswith(key)
            }
            if len(candidates) == 1:
                (action,) = candidates.values()
                self_contained = bool(eq)
        if action is None and len(token) > 2 and token[0] == "-" and token[1] != "-":
            # A cluster of short options (`-vv`, `-fVALUE`, `-vfVALUE`) is
            # hoisted whole when `_short_cluster` recognizes every character.
            cluster = _short_cluster(known, token)
            if cluster is not None:
                action, self_contained = cluster

        if self_contained:
            flags.append(token)
            i += 1
            continue

        if action is not None:
            # Every zero-value action class (store_true/store_false/count/help)
            # already reports `nargs == 0` -- no need to also isinstance-check
            # the specific classes.
            if action.nargs == 0:
                flags.append(token)
                i += 1
                continue
            if action.nargs is not None:
                # The flag's own nargs is variable or a fixed count: a variadic
                # FLAG before positionals is ambiguous for argparse itself, and
                # reordering cannot fix it. Bail rather than risk swallowing a
                # positional's token.
                return argv
            if i + 1 >= n:
                # A flag needing exactly one value, with none left in argv --
                # malformed input. Bail: let the REAL parse produce its own
                # error rather than guessing here.
                return argv
            flags.append(token)
            flags.append(argv[i + 1])
            i += 2
            continue

        if token.startswith("-") and len(token) > 1:
            looks_negative_number = bool(
                negative_number_matcher and negative_number_matcher.match(token)
            )
            if looks_negative_number and not has_negative_number_optionals:
                # A negative-number-shaped token with no negative-number-like
                # OPTIONAL to collide with is a positional value (as argparse
                # decides).
                positionals.append(token)
                i += 1
                continue
            # Unrecognized `-` token, a typo or something not handled here:
            # bail, and let the real parse report it.
            return argv

        positionals.append(token)
        i += 1

    return flags + positionals


def _suppress_inherited_defaults(child_parser, root_dests, root_defaults=None):
    """Make a subcommand's inherited-option defaults not clobber the root's value.

    For each flagged action on ``child_parser`` whose ``dest`` is in
    ``root_dests``, the default becomes ``SUPPRESS``, so an option given before
    the subcommand keeps the value the root parsed. Positionals and the
    subparsers action are untouched. A required root option becomes
    non-required on the child (the root enforces it) but still renders as
    required in usage.

    ``root_defaults`` (``{dest: effective_default}``) skips a dest the child
    deliberately re-declares with a different default, which must win.
    ``value_sources`` and config layering work on the root's actions and are
    unaffected.
    """
    root_defaults = root_defaults or {}
    for action in child_parser._actions:
        if action.dest not in root_dests:
            continue
        if not action.option_strings:  # positional
            continue
        if getattr(action, "required", False):
            if root_defaults.get(action.dest) is not NOT_DEFINED:
                continue
            # The root enforces a required option it declares itself, so the
            # child's copy must not demand it again after the subcommand name.
            action.required = False
            action.default = _argparse.SUPPRESS
            action._duho_display_required_ = True  # type: ignore[attr-defined]
            from ..formatters import _install_required_usage_formatter

            _install_required_usage_formatter(child_parser)
            continue
        if (
            action.dest in root_defaults
            and action.default != root_defaults[action.dest]
        ):
            # The child re-declares this field with a different default -- a
            # deliberate override; keep it.
            continue
        action.default = _argparse.SUPPRESS


def _set_private_default(child_parser, dest, value) -> None:
    """Give ``child_parser`` its own copy of each option action for ``dest``, defaulting to ``value``.

    A subparser built with ``parents=[...]`` shares the root's action objects with
    every sibling, so changing ``action.default`` in place changes it for all of
    them; the copy takes the child's place in the parser's registries instead.
    """
    for index, action in enumerate(child_parser._actions):
        if action.dest != dest or not action.option_strings:
            continue
        private = _copy.copy(action)
        private.default = value
        child_parser._actions[index] = private
        for option_string in action.option_strings:
            child_parser._option_string_actions[option_string] = private
        for group in child_parser._action_groups:
            actions = group._group_actions
            if action in actions:
                actions[actions.index(action)] = private
        for group in child_parser._mutually_exclusive_groups:
            actions = group._group_actions
            if action in actions:
                actions[actions.index(action)] = private


def _argv_before_subcommand(
    parser: _argparse.ArgumentParser, argv: list[str]
) -> list[str]:
    """Return the part of ``argv`` the root parser itself consumes.

    Stops at the first token that names one of ``parser``'s subcommands and is
    not the value of a preceding root option. Returns ``argv`` unchanged when
    the parser has no subcommands, a ``--`` comes first, or an option whose
    argument count cannot be told in advance precedes the name.
    """
    names = {
        name
        for action in parser._actions  # type: ignore[attr-defined]
        if isinstance(action, _argparse._SubParsersAction)  # type: ignore[attr-defined]
        for name in action.choices
    }
    if not names:
        return argv
    known = parser._option_string_actions  # type: ignore[attr-defined]
    allow_abbrev = getattr(parser, "allow_abbrev", True)
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--":
            return argv
        if token in names:
            return argv[:i]
        if len(token) > 1 and token[0] == "-":
            key, eq, _ = token.partition("=")
            action = known.get(key)
            if action is None and key.startswith("--") and allow_abbrev:
                matches = {
                    id(a): a
                    for opt, a in known.items()
                    if opt.startswith("--") and opt.startswith(key)
                }
                if len(matches) == 1:
                    (action,) = matches.values()
            if action is None or eq or (token[1] != "-" and len(token) > 2):
                i += 1
            elif action.nargs == 0:
                i += 1
            elif action.nargs is None:
                i += 2
            elif isinstance(action.nargs, int):
                i += 1 + action.nargs
            else:
                return argv
            continue
        i += 1
    return argv


def _literal_value_flags(parser: _argparse.ArgumentParser) -> frozenset[str]:
    """Option strings of every ``literal_value`` option in ``parser``'s whole tree.

    Computed once per parser object, on its first parse.
    """
    cached = getattr(parser, "_duho_literal_flags_", None)
    if cached is not None:
        return cached
    found: set[str] = set()
    seen: set[int] = set()
    pending = [parser]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for action in current._actions:  # type: ignore[attr-defined]
            if isinstance(action, _argparse._SubParsersAction):  # type: ignore[attr-defined]
                pending.extend(action.choices.values())
            elif getattr(action, "_duho_literal_value_", False):
                found.update(action.option_strings)
    result = frozenset(found)
    parser._duho_literal_flags_ = result  # type: ignore[attr-defined]
    return result


def _join_literal_values(argv: list[str], flags: frozenset[str]) -> list[str]:
    """Join each literal-value option with the token after it (``--k=V`` / ``-kV``).

    Stops at the first bare ``--`` that is not such an option's value, leaving
    it and everything after it untouched. A short option followed by an empty
    value is left as it is, since ``-k`` + ``""`` would not keep its value.
    """
    out: list[str] = []
    i = 0
    n = len(argv)
    while i < n:
        token = argv[i]
        if token == "--":
            out.extend(argv[i:])
            return out
        if token in flags and i + 1 < n:
            value = argv[i + 1]
            if token.startswith("--"):
                out.append(token + "=" + value)
                i += 2
                continue
            if value:
                out.append(token + value)
                i += 2
                continue
        out.append(token)
        i += 1
    return out


def _insert_default_subcommand(
    parser: _argparse.ArgumentParser, argv: list[str], default: str
) -> list[str]:
    """Insert ``default`` before the first token that is not one of ``parser``'s own options.

    The scan skips this parser's registered options and their values. Returns
    ``argv`` unchanged when the first remaining token already names one of the
    parser's subcommands, when no such token exists, or when any token before it
    cannot be classified with certainty (an unregistered option, a ``--``, an
    option taking a variable number of values, or a missing value).
    """
    names = {
        name
        for action in parser._actions  # type: ignore[attr-defined]
        if isinstance(action, _argparse._SubParsersAction)  # type: ignore[attr-defined]
        for name in action.choices
    }
    if not names:
        return argv
    known = parser._option_string_actions  # type: ignore[attr-defined]
    allow_abbrev = getattr(parser, "allow_abbrev", True)
    negative_number_matcher = getattr(parser, "_negative_number_matcher", None)
    has_negative_number_optionals = bool(
        getattr(parser, "_has_negative_number_optionals", [])
    )
    i = 0
    n = len(argv)
    while i < n:
        token = argv[i]
        if token == "--":
            return argv
        if len(token) < 2 or token[0] != "-":
            break
        if (
            negative_number_matcher
            and negative_number_matcher.match(token)
            and not has_negative_number_optionals
        ):
            break
        key, eq, _ = token.partition("=")
        action = known.get(key)
        attached = bool(eq)
        if action is None and key.startswith("--") and allow_abbrev:
            matches = {
                id(a): a
                for opt, a in known.items()
                if opt.startswith("--") and opt.startswith(key)
            }
            if len(matches) == 1:
                (action,) = matches.values()
        if action is None and len(token) > 2 and token[1] != "-" and not eq:
            cluster = _short_cluster(known, token)
            if cluster is None:
                return argv
            action, attached = cluster
        if action is None:
            return argv
        if action.nargs == 0:
            i += 1
        elif action.nargs is None:
            if attached:
                i += 1
            elif i + 1 >= n:
                return argv
            else:
                i += 2
        else:
            return argv
    if i >= n or argv[i] in names:
        return argv
    return argv[:i] + [default] + argv[i:]
