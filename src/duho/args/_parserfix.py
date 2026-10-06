import argparse as _argparse
import copy as _copy
import typing as _ty

from ._meta import NOT_DEFINED, NS

#: `nargs` values that make a positional variable-arity -- the shape that
#: triggers argparse's greedy positional-run-matching papercut (bpo-15112)
#: when ANOTHER positional sits in the same parser. Verified this session
#: (bare stdlib, no duho): `"*"` and `"?"` fail identically for a flag placed
#: between the two positionals; `"+"` fails too, just at a different arg
#: count (2+ trailing values instead of 1) -- all three are the trigger set.
_VARIADIC_NARGS = ("*", "+", "?")


def _has_variadic_positional(parser: "_argparse.ArgumentParser") -> bool:
    """True if ``parser`` has at least one variable-arity positional.

    Two DIFFERENT argparse papercuts both need this reorder, and between them
    a single variable-arity positional is already enough to trigger one:

    * A flag placed BETWEEN a variable-arity positional and ANOTHER
      positional breaks under argparse's greedy positional-run matching
      (bpo-15112): the run is settled against the argv slice before the next
      optional token, so the variadic one can close out empty/short and
      never reopen.
    * A flag placed anywhere touching a LONE variable-arity positional's own
      run -- with no sibling positional at all -- ALSO gets swallowed as
      "unrecognized arguments" (bpo-14191); a previous version of this
      docstring claimed a lone variadic positional was unaffected, which was
      not actually true (verified this session, bare stdlib).

    Both shapes are handled by the same reorder pass below, so this only
    needs to detect "at least one variable-arity positional" -- no sibling
    required.

    The subparsers action itself (``dest="_duho_command_"``, added by
    ``add_subparsers``) is NOT option-string-bearing either, but it is not a
    user-declared positional field -- excluded explicitly so a root class
    with subcommands and no OTHER declared positional doesn't false-trigger.
    """
    positionals = [
        action
        for action in parser._actions  # type: ignore[attr-defined]
        if not action.option_strings
        and not isinstance(action, _argparse._SubParsersAction)  # type: ignore[attr-defined]
    ]
    return any(action.nargs in _VARIADIC_NARGS for action in positionals)


def _keep_attached_double_dash(parser: "_argparse.ArgumentParser") -> None:
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


def _patch_parser_for_reorder(parser: "_argparse.ArgumentParser") -> None:
    """Install JUST the flag-between-positionals reorder on a plain parser.

    For parsers built outside ``Args._initparser_`` -- a module command's
    subparser (``runtime._register_module_command``), built via a bare
    ``subparsers.add_parser(...)`` -- so they never get the patched
    ``parse_known_args`` a declarative ``Args``/``Cmd`` subparser does. That
    parser needs neither ``"#cls"`` dispatch nor ``_passthrough_`` splitting
    (a module command has no subclass to select and the root already owns the
    ``--`` split), only this one fix, so a full ``_initparser_`` call would be
    the wrong tool -- it would also try to construct an ``Args`` instance from
    a parser that was never declared as one.
    """
    real_parse_known_args = parser.parse_known_args

    def parse_known_args(
        args: "_ty.Sequence[str] | None" = None, namespace: "NS | None" = None
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
    parser: "_argparse.ArgumentParser", argv: "list[str]"
) -> "list[str]":
    """Hoist recognized flags (+ their values) before the positional run.

    Preprocessing ONLY -- never decides an input is invalid. Scans ``argv``
    against ``parser``'s OWN, already-built ``_option_string_actions``
    registry; a token recognized there (bare, or via its ``key`` half of a
    ``--flag=value`` form) is hoisted, along with the value(s) its action's
    ``nargs``/type says it consumes, into a leading ``flags`` run. Everything
    else stays in a trailing ``positionals`` run, in original relative order.
    Concatenating ``flags + positionals`` gives argparse a slice where the
    positional run is contiguous and never interrupted by a flag, sidestepping
    the greedy-matching papercut this function exists for.

    Recognizes a flag by exact key, by its ``--flag=value`` split, by an
    attached short-option value (``-fVALUE``), and by an unambiguous
    ``allow_abbrev`` long-option prefix (``--filt`` for ``--filter``)
    -- the same spellings argparse itself accepts, so a flag written that way
    between two positionals is hoisted exactly like its long/separate-token
    form is.

    **Bails (returns ``argv`` UNCHANGED) on anything it isn't certain about**
    -- a `-`-prefixed token NOT recognized by any of the above (and not a
    negative-number value the parser itself would accept, per its own
    ``_negative_number_matcher``/``_has_negative_number_optionals``), or a
    flag needing a value with none left in ``argv``. This is deliberate: a
    genuine typo (``--filtr`` for ``--filter``) must still surface argparse's
    own honest "unrecognized arguments" error through the UNMODIFIED real
    parse, never get silently absorbed as a phantom positional value. This
    function only ever makes a VALID input parse correctly, or gets out of
    the way entirely.
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

    flags: "list[str]" = []
    positionals: "list[str]" = []
    i = 0
    n = len(argv)
    while i < n:
        token = argv[i]
        if token in subcommand_names:
            # Everything after a subcommand name belongs to the subparser.
            positionals.extend(argv[i:])
            break
        if token == "--":
            # A literal `--` separator is never part of THIS parser's own
            # flag/positional grammar (duho's `_passthrough_` handling
            # already splits argv on the FIRST `--` before this function
            # ever sees it -- see `_initparser_`'s patched
            # `parse_known_args` -- so reaching one here means a SECOND
            # `--`, which is itself part of the payload). Stop reordering;
            # everything from here on is left exactly as-is.
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
            # An unambiguous long-option PREFIX under argparse's own
            # `allow_abbrev` rule (e.g. `--filt` for `--filter`, or
            # `--filt=value` for `--filter=value`) -- recognized only when
            # exactly one ACTION (aliases of the same one still count as
            # one) has an option string starting with this token's flag
            # half (the part before `=`, when present).
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
                # The flag's OWN nargs is variable (`"*"`/`"+"`/`"?"`) or an
                # explicit fixed count -- e.g. a variadic positional's own
                # `nargs="*"`, or an OPTION field given an explicit
                # `NS(nargs="*")` override. Confirmed (bare stdlib): a
                # variadic-nargs FLAG placed
                # directly before positional values, with no separator, is
                # AMBIGUOUS FOR ARGPARSE ITSELF -- even correctly-ordered
                # argv silently misparses (extra tokens get absorbed into
                # the flag's own value list instead of the positional they
                # belonged to; see `docs/guide/arguments.md` /
                # `AGENTS.md`'s note on this shape). Reordering cannot
                # resolve an ambiguity the REAL parser cannot resolve either
                # -- bail unreordered rather than guess and risk swallowing
                # a token that belonged to a positional.
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
                # A negative-number-shaped token this parser has no
                # negative-number-shaped OPTIONAL to collide with -- treat
                # as a genuine positional value (mirrors argparse's own
                # `_negative_number_matcher` logic for the same decision).
                positionals.append(token)
                i += 1
                continue
            # An unrecognized `-`-prefixed token. Could be a real typo, or a
            # flag this reorder pass doesn't understand -- either way, NOT
            # confident enough to reorder. Bail unchanged; the real parse
            # surfaces its own honest error.
            return argv

        positionals.append(token)
        i += 1

    return flags + positionals


def _suppress_inherited_defaults(child_parser, root_dests, root_defaults=None):
    """Make a subcommand's inherited-option defaults not clobber the root's value.

    For each action on ``child_parser`` whose ``dest`` is also a field declared
    on the root (``root_dests``), set the action's default to ``SUPPRESS`` so
    that, when the flag is absent from the subcommand's argv, argparse leaves the
    namespace value the root already parsed (from an option given before the
    subcommand) intact. Only flagged actions are touched -- positionals keep
    their behavior, and the
    subparsers action itself (``dest="_duho_command_"``) is never a root field.
    A required option of the root is made non-required on the child (the root
    enforces it) and keeps rendering as required in the child's usage.

    ``root_defaults`` (optional ``{dest: effective_default}``) lets the caller
    skip suppression for a dest the child DELIBERATELY re-declares with a default
    differing from the root's: that override is intentional and must win, so the
    child keeps its own default rather than deferring to the root.

    ``value_sources`` / config layering are unaffected: they operate on the root
    parser's own actions, not these child copies.
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
    parser: "_argparse.ArgumentParser", argv: "list[str]"
) -> "list[str]":
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


def _literal_value_flags(parser: "_argparse.ArgumentParser") -> "frozenset[str]":
    """Option strings of every ``literal_value`` option in ``parser``'s whole tree.

    Computed once per parser object, on its first parse.
    """
    cached = getattr(parser, "_duho_literal_flags_", None)
    if cached is not None:
        return cached
    found: "set[str]" = set()
    seen: "set[int]" = set()
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


def _join_literal_values(argv: "list[str]", flags: "frozenset[str]") -> "list[str]":
    """Join each literal-value option with the token after it (``--k=V`` / ``-kV``).

    Stops at the first bare ``--`` that is not such an option's value, leaving
    it and everything after it untouched. A short option followed by an empty
    value is left as it is, since ``-k`` + ``""`` would not keep its value.
    """
    out: "list[str]" = []
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
    parser: "_argparse.ArgumentParser", argv: "list[str]", default: str
) -> "list[str]":
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
