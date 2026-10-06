import argparse as _argparse
import dataclasses as _dc
import pathlib as _pathlib

from .. import parsers as _parsers


@_dc.dataclass
class CompletionOption:
    """One optional argument (e.g. ``--name``/``-n``)."""

    flags: "tuple[str, ...]"
    takes_value: bool
    choices: "tuple[str, ...] | None" = None
    is_path: bool = False


@_dc.dataclass
class CompletionPositional:
    """One positional argument."""

    name: str
    choices: "tuple[str, ...] | None" = None
    is_path: bool = False
    #: Hidden via ``help=argparse.SUPPRESS``: still occupies its ordinal slot
    #: (every emitter counts positions sequentially to know which one is
    #: pending), but offers no candidates of its own. Field goes LAST so
    #: positional construction of the other fields is unaffected.
    hidden: bool = False


@_dc.dataclass
class CompletionSpec:
    """Shell-agnostic view of a single (sub)parser and its subcommand tree."""

    # Field order matters: this is a plain dataclass, so positional
    # construction binds by position. `prog` through `help` matches the
    # pre-existing order exactly; `path` (added later) goes LAST instead of
    # in 2nd position, so it no longer shifts every field after it.
    prog: str
    options: "list[CompletionOption]" = _dc.field(default_factory=list)
    positionals: "list[CompletionPositional]" = _dc.field(default_factory=list)
    subcommands: "dict[str, CompletionSpec]" = _dc.field(default_factory=dict)
    #: One-line help for THIS (sub)command, used as the fish ``-d`` description.
    help: str = ""
    #: The subcommand names from the root down to THIS spec, e.g. ``("Db",
    #: "Migrate")``; ``()`` for the root. Used by every emitter as the join
    #: key for "which node am I completing" lookups.
    path: "tuple[str, ...]" = ()


def _is_path_type(action: _argparse.Action) -> bool:
    ty = getattr(action, "type", None)
    return isinstance(ty, type) and issubclass(ty, _pathlib.Path)


def _enum_choices(type_factory: object) -> "tuple[str, ...] | None":
    """Recover ``enum.Enum`` member names from a duho Enum-field factory.

    duho's Enum branch leaves ``action.choices`` unset and gives the field a
    resolving factory that carries its canonical member names as
    ``_duho_choices_``. Any other factory (a plain or custom type) yields
    ``None``.
    """
    names = getattr(type_factory, "_duho_choices_", None)
    if not isinstance(names, tuple) or not names:
        return None
    if not all(isinstance(n, str) for n in names):
        return None
    return names


def _drop_nul_choices(choices: "tuple[str, ...] | None") -> "tuple[str, ...] | None":
    """Drop any candidate containing a NUL byte.

    A NUL can't be represented in any of the four shells' word lists (bash
    ``compgen -W``, zsh ``_arguments``, fish ``complete -a``, PowerShell's
    completion array all terminate or corrupt on it). Completion is
    advisory -- the parser still validates the real value -- so a bad
    candidate is silently omitted rather than breaking completion for every
    OTHER candidate on the same field.
    """
    if not choices:
        return None
    filtered = tuple(c for c in choices if "\0" not in c)
    return filtered or None


def _choices_tuple(action: _argparse.Action) -> "tuple[str, ...] | None":
    choices = getattr(action, "choices", None)
    if choices:
        return _drop_nul_choices(tuple(str(c) for c in choices))
    return _drop_nul_choices(_enum_choices(getattr(action, "type", None)))


def _takes_value(action: _argparse.Action) -> bool:
    # nargs == 0 means a flag-style action (store_true/store_false/help/
    # version/store_const/etc.) that never consumes a value.
    return action.nargs != 0


def _walk(
    parser: _argparse.ArgumentParser,
    prog: "str | None" = None,
    path: "tuple[str, ...]" = (),
) -> CompletionSpec:
    """Recursively turn a built ArgumentParser into a CompletionSpec.

    Pure data, no shell strings -- shared by all four emitters below.
    """
    spec = CompletionSpec(prog=prog or parser.prog, path=path)

    subparsers_action = _parsers.find_subparsers(parser)
    for action in parser._actions:
        if action is subparsers_action:
            continue
        is_positional = not action.option_strings
        hidden = getattr(action, "help", None) is _argparse.SUPPRESS
        if hidden and not is_positional:
            # A hidden OPTION carries no ordinal position, so it can simply
            # be omitted from completion entirely.
            continue
        if is_positional:
            # A hidden POSITIONAL still occupies its ordinal slot -- every
            # emitter counts positions sequentially to know which one is
            # pending, so dropping it here would shift every later
            # positional's completions one slot early. Keep the entry, just
            # with no candidates of its own.
            spec.positionals.append(
                CompletionPositional(
                    name=action.dest,
                    choices=None if hidden else _choices_tuple(action),
                    is_path=False if hidden else _is_path_type(action),
                    hidden=hidden,
                )
            )
            continue
        spec.options.append(
            CompletionOption(
                flags=tuple(action.option_strings),
                takes_value=_takes_value(action),
                choices=_choices_tuple(action),
                is_path=_is_path_type(action),
            )
        )

    if subparsers_action is not None:
        # argparse keeps each subcommand's one-line help in the pseudo-actions,
        # not on the subparser -- capture it here for the fish `-d` description,
        # and skip any subcommand hidden via `help=argparse.SUPPRESS`. A
        # pseudo-action exists only for the PRIMARY name passed to
        # `add_parser` (never per-alias), so suppressing by that name alone
        # leaves an alias of a hidden subcommand completable; key suppression
        # off the underlying parser object instead, since every alias of the
        # same subcommand maps to the same parser.
        help_by_name: "dict[object, str]" = {}
        suppressed_dests: "set[object]" = set()
        choices = subparsers_action.choices or {}
        for a in getattr(subparsers_action, "_choices_actions", []):
            dest = getattr(a, "dest", None)
            if getattr(a, "help", None) is _argparse.SUPPRESS:
                suppressed_dests.add(dest)
            else:
                help_by_name[dest] = getattr(a, "help", None) or ""
        suppressed_parsers = {
            id(choices[dest]) for dest in suppressed_dests if dest in choices
        }
        for name, subparser in choices.items():
            if id(subparser) in suppressed_parsers:
                continue
            sub_spec = _walk(subparser, prog=f"{spec.prog} {name}", path=path + (name,))
            sub_spec.help = help_by_name.get(name, "")
            spec.subcommands[name] = sub_spec

    return spec


def spec(parser: _argparse.ArgumentParser, prog: "str | None" = None) -> CompletionSpec:
    """Build the shell-agnostic :class:`CompletionSpec` tree for ``parser``.

    Public wrapper around the internal parser-tree walk: the
    dataclasses are documented and exported, but until now the only producer
    was the private `_walk`, so a caller writing a completion emitter for a
    shell this module doesn't cover (nushell, elvish, ...) had no supported
    way to get one. Additive, so a PATCH release pre-1.0.
    """
    return _walk(parser, prog=prog)


def _all_specs(root: CompletionSpec) -> "list[CompletionSpec]":
    """Flatten a spec tree into a list (root first, depth-first)."""
    result = [root]
    for sub in root.subcommands.values():
        result.extend(_all_specs(sub))
    return result


def _cmd_key(spec: CompletionSpec) -> str:
    """The command-path lookup key for ``spec``: its subcommand path joined
    with a single space -- consulted by bash/PowerShell as they walk
    the typed-so-far command line to find "which node am I completing"."""
    return " ".join(spec.path)


def _value_flag_names(spec: CompletionSpec) -> "list[str]":
    """This spec's OWN value-taking option flags, sorted: used
    per-command-path, never merged across levels, so a flag that is a
    boolean at one level and value-taking at another is resolved correctly
    at each level independently."""
    return sorted({f for opt in spec.options for f in opt.flags if opt.takes_value})


def _flag_names(spec: CompletionSpec) -> "list[str]":
    """This spec's own flags (any arity), sorted."""
    return sorted({f for opt in spec.options for f in opt.flags})


def _func_name(prog: str) -> str:
    """Turn a (possibly multi-word, sub-command-qualified) prog into a
    shell-safe identifier fragment."""
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in prog)
