"""Shell completion script generation (bash/zsh/fish/powershell).

**Decision (do not revisit): STATIC script generation** -- these functions
emit a self-contained completion script the user installs once, NOT a
dynamic argcomplete-style hook that re-invokes the program on every Tab.
Zero runtime dependency, zero per-invocation cost: a core differentiator
vs. argcomplete.

All four emitters (`bash`, `zsh`, `fish`, `powershell`) share one parser-tree
walk (`_walk`) that turns a *built* `argparse.ArgumentParser` into a plain,
shell-agnostic `CompletionSpec`. Only the emitters know shell syntax.

Completion data is read off the built parser's private attrs
(`parser._actions`, `parser._subparsers`) -- the same internal contract
`parsers.py` already relies on elsewhere in this codebase.

**Two-level quoting.** A value (a choice, a subcommand name, a flag) can pass
through TWO parses: the STATIC parse when the shell first reads/sources the
generated script, and -- for zsh's `_arguments` action lists and fish's
`complete -a`/`-n` arguments -- a SECOND, dynamic evaluation every time the
user presses Tab. A quoter that only survives the first parse (bash's
`_bash_wordlist`, zsh/fish's old shared `_sq`) does not protect the second.
Every site that reaches a second evaluation therefore escapes each value for
that evaluation FIRST (`_zsh_word` / the fish-word escaper below), then wraps
the result for the static parse (`_sq` / `_fsq`).
"""

import argparse as _argparse
import dataclasses as _dc
import hashlib as _hashlib
import pathlib as _pathlib
import shlex as _shlex

from . import parsers as _parsers

__all__ = [
    "CompletionOption",
    "CompletionPositional",
    "CompletionSpec",
    "spec",
    "bash",
    "zsh",
    "fish",
    "powershell",
]


def _bashq(value: object) -> str:
    """POSIX-shell-quote a single value used as a direct bash argument."""
    return _shlex.quote(str(value))


def _bash_wordlist(values: "list") -> str:
    """Build a safe ``compgen -W`` word-list argument from ``values``.

    ``compgen -W`` gives its word-list argument a SECOND evaluation at
    Tab-press: after the shell parses/sources the generated script, ``compgen``
    itself re-splits and re-expands that argument's runtime VALUE as if it
    were freshly typed input (command substitution, parameter expansion, word
    splitting, quote removal -- all of it). Backslash-escaping
    ``\\``/``$``/`` ` `` in each value neutralises the expansion triggers a
    hostile choice like ``$(rm -rf ~)`` would otherwise run; escaping ``'``
    and ``\"`` too stops an embedded quote from opening a SECOND, unmatched
    quoted region at that re-evaluation, which would otherwise swallow every
    later value (space and all) into one candidate. Escaping the value list
    is not enough on its own: the joined values still ride inside one
    single-quoted argument for THIS (the static) parse, so once each value is
    safe for the second pass, single-quote the whole list (embedded single
    quotes as ``'\\''``) to survive the first.
    """
    escaped: "list[str]" = []
    for value in values:
        s = str(value)
        for ch in ("\\", "$", "`", "'", '"'):
            s = s.replace(ch, "\\" + ch)
        escaped.append(s)
    joined = " ".join(escaped)
    return "'" + joined.replace("'", "'\\''") + "'"


def _sq(value: object) -> str:
    """Single-quote a value for a zsh single-quoted context (the STATIC parse).

    An embedded single quote is closed, escaped, and reopened (``'\\''``) so a
    choice like ``it's`` cannot break out of the surrounding quotes at the
    point zsh first reads the script. This is the *outer* layer only: any
    value zsh's `_arguments` evaluates a second time (an action's `(a b c)`
    list, a `1:command:(...)` name list) must ALSO go through `_zsh_word`
    first, or the second evaluation can still run it.
    """
    return "'" + str(value).replace("'", "'\\''") + "'"


_ZSH_WORD_SAFE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./+,=@%-"
)


def _zsh_word(value: object) -> str:
    """Escape ``value`` for zsh's SECOND (dynamic) evaluation.

    zsh's ``_arguments`` builds an action list like ``(a b c)`` with
    ``eval``, and splits a message/action spec on ``:``. Backslash-escape
    every character outside a conservative safe set -- the same approach
    zsh's own ``${(q)}`` quoting uses -- so a value survives that second
    pass literally: whitespace, ``$`` `` ` `` ``()[]{}`` ``;|&<>`` ``'"``
    ``*?~#^!`` and ``:`` all become a literal character instead of shell
    syntax. Apply this FIRST, then wrap the joined result in `_sq` for the
    static parse.
    """
    return "".join(c if c in _ZSH_WORD_SAFE else "\\" + c for c in str(value))


# Unlike zsh, fish expands a bare `%self`/`%<job>` job-id token even inside a
# value that has otherwise been through this escaper, so `%` cannot share
# zsh's safe set: it must always be backslash-escaped.
_FISH_WORD_SAFE = _ZSH_WORD_SAFE - frozenset("%")


def _fish_word(value: object) -> str:
    """Escape ``value`` for fish's SECOND (dynamic) evaluation.

    fish's ``complete -a``/``-n`` arguments are tokenized and expanded again
    at completion time -- including ``$(...)``/``(...)`` command
    substitution. Backslash-escape every character outside a conservative
    safe set so a value (a choice, a subcommand name used in an
    ``__fish_seen_subcommand_from`` condition) survives that pass literally.
    Apply this FIRST, then wrap the joined/assembled result in `_fsq` for
    the static parse.
    """
    return "".join(c if c in _FISH_WORD_SAFE else "\\" + c for c in str(value))


def _fsq(value: object) -> str:
    """Single-quote a value for a fish single-quoted context (the STATIC parse).

    Inside fish single quotes, only ``\\\\`` and ``\\'`` are recognised
    escapes (unlike POSIX/zsh, where a single-quoted string has no escapes
    at all) -- a value ending in an odd number of backslashes would
    otherwise leave the quote open and fish refuses to source the rest of
    the file. Escape backslash first, then the quote.
    """
    s = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return "'" + s + "'"


def _psq(value: object) -> str:
    """Single-quote a value for a PowerShell single-quoted string literal.

    PowerShell escapes an embedded single quote by *doubling* it (``''``), the
    only metacharacter live inside a single-quoted literal. When ``value``
    contains a non-ASCII character, emit a pure-ASCII expression instead
    (ASCII runs as quote-doubled literals, concatenated with
    ``[char]0xNNNN`` for each non-ASCII UTF-16 code unit) rather than the raw
    character: PowerShell decodes a native command's stdout with the
    console's OEM code page, which duho cannot control, so non-ASCII text
    piped through ``| Out-String | Invoke-Expression`` can arrive mangled
    even though this Python process wrote correct UTF-8/text. A pure
    ASCII script sidesteps the console code page entirely, on both Windows
    PowerShell 5.1 and pwsh 7. This protects only the *script body* -- see
    `powershell`'s docstring for what protects the *inserted candidate text*
    at Tab-time.
    """
    text = str(value)
    if text.isascii():
        return "'" + text.replace("'", "''") + "'"
    parts: "list[str]" = []
    run = ""
    for ch in text:
        if ch.isascii():
            run += ch
        else:
            if run:
                parts.append("'" + run.replace("'", "''") + "'")
                run = ""
            for code_unit in _utf16_units(ch):
                parts.append("[char]0x%04X" % code_unit)
    if run:
        parts.append("'" + run.replace("'", "''") + "'")
    if not parts:
        return "''"
    return "(" + " + ".join(parts) + ")"


def _utf16_units(ch: str) -> "list[int]":
    """The UTF-16 code unit(s) for a single ``str`` character (surrogate pair
    for an astral character, one unit otherwise)."""
    encoded = ch.encode("utf-16-le")
    return [encoded[i] | (encoded[i + 1] << 8) for i in range(0, len(encoded), 2)]


def _validate_prog(prog: str) -> str:
    """Validate a program name used unquoted as a completion function target.

    ``prog`` names a shell function (``_<prog>``) and the completed command; a
    value with whitespace or shell metacharacters would corrupt the generated
    script, so it is rejected with a clear error. A normal prog (letters,
    digits, ``_``/``-``/``.``) passes untouched.
    """
    if any(c.isspace() for c in prog):
        raise ValueError(
            "completion: program name %r contains whitespace; refusing to emit "
            "a completion script for it" % prog
        )
    unsafe = set(prog) & set("\"'`$&;|<>(){}[]*?!\\\n\r\t")
    if unsafe:
        raise ValueError(
            "completion: program name %r contains shell metacharacter(s) %s; "
            "refusing to emit a completion script for it"
            % (prog, "".join(sorted(unsafe)))
        )
    return prog


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


def _choices_tuple(action: _argparse.Action) -> "tuple[str, ...] | None":
    choices = getattr(action, "choices", None)
    if choices:
        return tuple(str(c) for c in choices)
    return _enum_choices(getattr(action, "type", None))


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


def _bash_func_name(root_prog: str) -> str:
    """A collision-resistant bash function name for ``root_prog``.

    The plain sanitised name alone collides across programs that only differ
    in punctuation (``my-app``/``my.app``/``my_app`` all sanitise to
    ``my_app``) and can shadow a bash-completion helper of the same name
    (``_filedir``, ``_files``, ...). Prefix with a fixed namespace and a
    short content hash of the *raw* prog so two differently-punctuated progs
    -- or a prog that happens to match a helper name -- get distinct
    functions.
    """
    safe = _func_name(root_prog)
    digest = _hashlib.sha1(root_prog.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    return f"_duho_complete_{safe}_{digest}"


# --------------------------------------------------------------------------
# bash
# --------------------------------------------------------------------------


def bash(parser: _argparse.ArgumentParser, prog: "str | None" = None) -> str:
    """Emit a self-contained bash completion script for `parser`.

    Descends the command line only on words that are real subcommand names
    of the CURRENT node (a per-path subcommand table, resolved as the walk
    goes); every other bare word counts as one of that node's
    own positionals, tracked by position so only the pending positional's
    own candidates (its `choices` via `compgen -W`, or `compgen -f` for a
    Path positional) are offered, not every positional's at once. A
    value-taking flag's value is skipped the same way, including the split
    `--opt = value` form bash produces for `--opt=value`; the
    value-flag set is resolved per command path, not merged globally.
    Every value-taking flag -- choice, Path, or free -- gets its own
    `$prev` arm. `COMPREPLY` is always filled via `mapfile` from a
    process-substitution `compgen` call, never `$(...)` splicing, so results
    are never word-split or glob-expanded a second time. Registered
    with `-o bashdefault -o default -o filenames` so bash's native filename
    completion applies whenever nothing above matches, and Path
    positionals/options get properly escaped/slashed directory names.
    """
    root = _walk(parser, prog=prog)
    root_prog = _validate_prog(root.prog)
    func = _bash_func_name(root_prog)
    specs = _all_specs(root)

    lines: "list[str]" = []
    lines.append(f"# bash completion for {root_prog}")
    lines.append(f"{func}() {{")
    lines.append("    local cur prev")
    lines.append("    COMPREPLY=()")
    lines.append('    cur="${COMP_WORDS[COMP_CWORD]}"')
    lines.append('    prev="${COMP_WORDS[COMP_CWORD-1]}"')
    lines.append("    # `--opt=value` arrives as the three words --opt, =, value")
    lines.append("    # (`=` is in COMP_WORDBREAKS); look one word further back.")
    lines.append('    if [ "$prev" = "=" ] && [ "$COMP_CWORD" -ge 2 ]; then')
    lines.append('        prev="${COMP_WORDS[COMP_CWORD-2]}"')
    lines.append("    fi")
    lines.append("    # `--opt=` with nothing typed yet arrives as --opt, = -- `cur`")
    lines.append(
        '    # IS the literal "=" (there is no fourth, empty word), which would'
    )
    lines.append("    # otherwise be used to filter candidates and match nothing.")
    lines.append('    if [ "$cur" = "=" ]; then')
    lines.append('        cur=""')
    lines.append("    fi")
    lines.append("")
    lines.append("    # Walk COMP_WORDS to find which (sub)command we are in: descend")
    lines.append("    # only on a word that is a REAL subcommand name of the current")
    lines.append("    # node, skip the value that follows a value-taking flag of the")
    lines.append("    # current node (including the split `--opt = value` form), and")
    lines.append("    # count every other bare word as one of the current node's own")
    lines.append("    # positionals.")
    lines.append('    local cmd_path=""')
    lines.append("    local npos=0")
    lines.append("    local i=1")
    lines.append("    local skip=0")
    lines.append("    local w is_vflag is_sub")
    lines.append("    while [ $i -lt $COMP_CWORD ]; do")
    lines.append('        w="${COMP_WORDS[i]}"')
    lines.append("        if [ $skip -gt 0 ]; then")
    lines.append("            skip=$((skip - 1))")
    lines.append("        else")
    lines.append('            case "$w" in')
    lines.append("                -*)")
    lines.append("                    is_vflag=0")
    lines.append('                    case "$cmd_path" in')
    for s in specs:
        vflags = _value_flag_names(s)
        if not vflags:
            continue
        pattern = "|".join(_bashq(f) for f in vflags)
        lines.append(f"                        {_bashq(_cmd_key(s))})")
        lines.append('                            case "$w" in')
        lines.append(f"                                {pattern}) is_vflag=1 ;;")
        lines.append("                            esac")
        lines.append("                            ;;")
    lines.append("                    esac")
    lines.append("                    if [ $is_vflag -eq 1 ]; then")
    lines.append('                        if [ "${COMP_WORDS[i+1]}" = "=" ]; then')
    lines.append("                            skip=2")
    lines.append("                        else")
    lines.append("                            skip=1")
    lines.append("                        fi")
    lines.append("                    fi")
    lines.append("                    ;;")
    lines.append("                *)")
    lines.append("                    is_sub=0")
    lines.append('                    case "$cmd_path" in')
    for s in specs:
        if not s.subcommands:
            continue
        pattern = "|".join(_bashq(n) for n in s.subcommands)
        lines.append(f"                        {_bashq(_cmd_key(s))})")
        lines.append('                            case "$w" in')
        lines.append(f"                                {pattern}) is_sub=1 ;;")
        lines.append("                            esac")
        lines.append("                            ;;")
    lines.append("                    esac")
    lines.append("                    if [ $is_sub -eq 1 ]; then")
    lines.append('                        cmd_path="${cmd_path:+$cmd_path }$w"')
    lines.append("                        npos=0")
    lines.append("                    else")
    lines.append("                        npos=$((npos + 1))")
    lines.append("                    fi")
    lines.append("                    ;;")
    lines.append("            esac")
    lines.append("        fi")
    lines.append("        i=$((i + 1))")
    lines.append("    done")
    lines.append("")

    for cspec in specs:
        key = _cmd_key(cspec)
        lines.append(f'    if [ "$cmd_path" = {_bashq(key)} ]; then')

        # prev-based value completion: EVERY value-taking flag gets an arm,
        # not just choice/Path ones, so a free-value flag never falls
        # through to the general candidate list.
        value_opts = [o for o in cspec.options if o.takes_value]
        if value_opts:
            lines.append('        case "$prev" in')
            for opt in value_opts:
                flag_pattern = "|".join(_bashq(f) for f in opt.flags)
                lines.append(f"            {flag_pattern})")
                if opt.choices:
                    words = _bash_wordlist(list(opt.choices))
                    lines.append(
                        f'                mapfile -t COMPREPLY < <(compgen -W {words} -- "$cur")'
                    )
                elif opt.is_path:
                    lines.append(
                        '                mapfile -t COMPREPLY < <(compgen -f -- "$cur")'
                    )
                else:
                    lines.append("                COMPREPLY=()")
                lines.append("                return 0 ;;")
            lines.append("        esac")

        flags = _flag_names(cspec)
        n_pos = len(cspec.positionals)
        lines.append('        case "$cur" in')
        lines.append("            -*)")
        if flags:
            words = _bash_wordlist(flags)
            lines.append(
                f'                mapfile -t COMPREPLY < <(compgen -W {words} -- "$cur")'
            )
        else:
            lines.append("                COMPREPLY=()")
        lines.append("                return 0 ;;")
        lines.append("        esac")

        if n_pos:
            lines.append('        case "$npos" in')
            for idx, pos in enumerate(cspec.positionals):
                lines.append(f"            {idx})")
                if pos.choices:
                    words = _bash_wordlist(list(pos.choices))
                    lines.append(
                        f'                mapfile -t COMPREPLY < <(compgen -W {words} -- "$cur")'
                    )
                elif pos.is_path:
                    lines.append(
                        '                mapfile -t COMPREPLY < <(compgen -f -- "$cur")'
                    )
                else:
                    lines.append("                COMPREPLY=()")
                lines.append("                return 0 ;;")
            lines.append("        esac")

        if cspec.subcommands:
            names = sorted(cspec.subcommands)
            words = _bash_wordlist(names)
            lines.append(f"        if [ $npos -eq {n_pos} ]; then")
            lines.append(
                f'            mapfile -t COMPREPLY < <(compgen -W {words} -- "$cur")'
            )
            lines.append("            return 0")
            lines.append("        fi")

        lines.append("        COMPREPLY=()")
        lines.append("        return 0")
        lines.append("    fi")

    lines.append("}")
    lines.append(
        f"complete -o bashdefault -o default -o filenames -F {func} {_bashq(root_prog)}"
    )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# zsh
# --------------------------------------------------------------------------


def _zsh_value_part(opt: "CompletionOption") -> str:
    """The ``:message:action`` tail of a zsh optspec for a value-taking option."""
    if opt.choices:
        values = " ".join(_zsh_word(c) for c in opt.choices)
        return f":value:({values})"
    if opt.is_path:
        return ":value:_files"
    return ":value:"


def _zsh_optspec(opt: "CompletionOption") -> str:
    """Build one zsh ``_arguments`` optspec for ``opt``.

    A single-flag option is ``<flag>'[desc]...'``; a multi-flag option uses the
    exclusion-list + brace-expansion form ``'(-v --verbose)'{-v,--verbose}'[desc]...'``.
    Every interpolated part is single-quoted with embedded-quote escaping,
    INCLUDING the flag(s) themselves: an unquoted flag
    interpolated raw into the script body can inject shell code the moment
    the script is sourced (a flag containing `$(...)`), not just at Tab-time.
    """
    tail_inner = "[option]"
    if opt.takes_value:
        tail_inner += _zsh_value_part(opt)
    tail = _sq(tail_inner)
    if len(opt.flags) == 1:
        return _sq(opt.flags[0]) + tail
    exclusion = _sq("(" + " ".join(opt.flags) + ")")
    brace = "{" + ",".join(_sq(f) for f in opt.flags) + "}"
    return exclusion + brace + tail


def _zsh_pos_spec(n: int, pos: "CompletionPositional") -> str:
    """Build one zsh positional spec: ``N:message:action`` (1-based position)
    -- NOT the ``name:name:action`` form the old emitter used, which
    `_arguments` rejects outright on every Tab."""
    name = _zsh_word(pos.name)
    if pos.choices:
        values = " ".join(_zsh_word(c) for c in pos.choices)
        return f"{n}:{name}:({values})"
    if pos.is_path:
        return f"{n}:{name}:_files"
    return f"{n}:{name}:"


def _zsh_funcid(func: str, path: "tuple[str, ...]") -> str:
    """The zsh function name for the node at ``path``: ``_<func>`` for the
    root, ``_<func>__<seg1>__<seg2>...`` for a nested node."""
    if not path:
        return f"_{func}"
    suffix = "__".join(_func_name(p) for p in path)
    return f"_{func}__{suffix}"


def zsh(parser: _argparse.ArgumentParser, prog: "str | None" = None) -> str:
    """Emit a `#compdef`-style zsh completion script for `parser`.

    Standard zsh subcommand dispatch: one function per (sub)command
    node. A node with subcommands calls `_arguments -C -s` with a numbered
    `N:command:(names)` positional followed by `*::arg:->args`, which makes
    `_arguments` itself shift `words`/`CURRENT` for the matched branch --
    the child node's own `_arguments` call then sees the subcommand name as
    `words[1]`, not an extra unexpected positional. Because the real
    `_arguments` engine parses options and positionals for us, a
    value-taking flag before the cursor never breaks completion and a
    positional value is never mistaken for a subcommand word.
    Positional specs use the required `N:message:action` form. Every choice,
    subcommand name and positional message is escaped for zsh's SECOND
    (dynamic) evaluation via `_zsh_word` before being wrapped in `_sq` for
    the first: a value containing `$(...)`, `;`, or a colon can no
    longer run code or break the spec at Tab-time.
    """
    root = _walk(parser, prog=prog)
    root_prog = _validate_prog(root.prog)
    func = _func_name(root_prog)

    lines: "list[str]" = []
    lines.append(f"#compdef {root_prog}")
    lines.append("")

    for cspec in _all_specs(root):
        funcid = _zsh_funcid(func, cspec.path)
        lines.append(f"{funcid} () {{")

        args_items: "list[str]" = [_zsh_optspec(opt) for opt in cspec.options]
        n = 0
        for pos in cspec.positionals:
            n += 1
            args_items.append(_sq(_zsh_pos_spec(n, pos)))
        if cspec.subcommands:
            lines.append('    local curcontext="$curcontext" state state_descr line')
            lines.append("    typeset -A opt_args")
            n += 1
            names = " ".join(_zsh_word(name) for name in cspec.subcommands)
            args_items.append(_sq(f"{n}:command:({names})"))
            args_items.append(_sq("*::arg:->args"))
            arguments_flags = "-C -s"
        else:
            arguments_flags = "-s"

        if args_items:
            lines.append(f"    _arguments {arguments_flags} \\")
            for idx, item in enumerate(args_items):
                sep = " \\" if idx < len(args_items) - 1 else ""
                lines.append(f"        {item}{sep}")
        else:
            lines.append(f"    _arguments {arguments_flags}")

        if cspec.subcommands:
            lines.append("")
            lines.append("    case $state in")
            lines.append("        args)")
            lines.append("            case $line[1] in")
            for name, sub in cspec.subcommands.items():
                subfuncid = _zsh_funcid(func, sub.path)
                # A quoted case label is matched literally (no glob
                # interpretation of a hostile name) -- reuse `_sq`.
                lines.append(f"                {_sq(name)})")
                lines.append(f"                    {subfuncid}")
                lines.append("                    ;;")
            lines.append("            esac")
            lines.append("            ;;")
            lines.append("    esac")

        lines.append("}")
        lines.append("")

    lines.append(f'{_zsh_funcid(func, ())} "$@"')
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# fish
# --------------------------------------------------------------------------


def _fish_func_name(root_prog: str) -> str:
    """A collision-resistant fish function name for ``root_prog``'s resolved-
    path helper, mirroring `_bash_func_name`'s hashing (distinct progs that
    sanitise the same way, or a prog matching a fish builtin/another
    completion's helper, still get distinct functions)."""
    safe = _func_name(root_prog)
    digest = _hashlib.sha1(root_prog.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    return f"__duho_complete_{safe}_{digest}_path"


def _fish_path_resolver(path_func: str, specs: "list[CompletionSpec]") -> "list[str]":
    """Emit a fish function that resolves the (sub)command path typed so far.

    `__fish_seen_subcommand_from <name>` only asks "does this word appear
    ANYWHERE on the command line", with no notion of position or depth: a
    subcommand name reused at a deeper level (root `run` vs. nested `db
    run`) makes the ROOT `run` node's own gate true too, leaking its flags
    into the nested one. Resolving the exact path by walking the command
    line -- exactly as the bash and PowerShell emitters already do -- and
    then gating on exact path equality removes the ambiguity entirely:
    `commandline -opc` gives fish's own tokenized, already-dequoted words up
    to the cursor, so (unlike bash/PowerShell) no extra quote-stripping is
    needed to recognise a subcommand name the user had to quote.
    """
    lines: "list[str]" = []
    lines.append(f"function {path_func}")
    lines.append("    set -l tokens (commandline -opc)")
    lines.append("    set -l cmd_path ''")
    lines.append("    set -l skip 0")
    lines.append("    set -l n (count $tokens)")
    lines.append("    for i in (seq 2 $n)")
    lines.append("        set -l w $tokens[$i]")
    lines.append("        if test $skip -gt 0")
    lines.append("            set skip (math $skip - 1)")
    lines.append("            continue")
    lines.append("        end")
    lines.append("        if string match -q -- '-*' $w")
    lines.append("            set -l is_vflag 0")
    for s in specs:
        vflags = _value_flag_names(s)
        if not vflags:
            continue
        lines.append(f'            if test "$cmd_path" = {_fsq(_cmd_key(s))}')
        cond = " -o ".join(f'"$w" = {_fsq(f)}' for f in vflags)
        lines.append(f"                if test {cond}")
        lines.append("                    set is_vflag 1")
        lines.append("                end")
        lines.append("            end")
    lines.append("            if test $is_vflag -eq 1")
    lines.append("                set skip 1")
    lines.append("            end")
    lines.append("        else")
    lines.append("            set -l is_sub 0")
    for s in specs:
        if not s.subcommands:
            continue
        lines.append(f'            if test "$cmd_path" = {_fsq(_cmd_key(s))}')
        cond = " -o ".join(f'"$w" = {_fsq(n)}' for n in s.subcommands)
        lines.append(f"                if test {cond}")
        lines.append("                    set is_sub 1")
        lines.append("                end")
        lines.append("            end")
    lines.append("            if test $is_sub -eq 1")
    lines.append('                if test -n "$cmd_path"')
    lines.append('                    set cmd_path "$cmd_path $w"')
    lines.append("                else")
    lines.append("                    set cmd_path $w")
    lines.append("                end")
    lines.append("            end")
    lines.append("        end")
    lines.append("    end")
    lines.append("    echo $cmd_path")
    lines.append("end")
    lines.append("")
    return lines


def _fish_condition(spec: CompletionSpec, path_func: str) -> str:
    """The `-n` gating condition for `spec`'s own completions: true exactly
    when the resolved command path (from `path_func`) equals this node's
    own path, so a node's flags and subcommand names appear only on its own
    exact path -- never above, below, or at a same-named sibling path."""
    return f"test ({path_func}) = {_fsq(_cmd_key(spec))}"


def fish(parser: _argparse.ArgumentParser, prog: "str | None" = None) -> str:
    """Emit a fish completion script (`complete -c <prog> ...` lines) for `parser`.

    Each rule is gated by `_fish_condition`, which resolves the exact
    (sub)command path via a generated helper function (see
    `_fish_path_resolver`) so a node's flags and subcommand names appear
    only on its own exact path -- not above, below, or at a same-named
    sibling path elsewhere in the tree. Choice/free-value options use `-x`
    (require a value, no file completion mixed in); Path-typed options keep
    `-r -F` for fish's native file completion. Every value reaching a `-a`
    or `-n` argument -- which fish tokenizes and expands AGAIN at
    completion time -- is escaped for that second pass with `_fish_word`
    before being wrapped for the static parse with `_fsq`, fish's own
    quoter that also escapes a trailing backslash: `it's`, `dry run`,
    `$(touch x)` and a Windows-style `C:\\` choice all round-trip as literal
    text instead of running or corrupting the file.
    """
    root = _walk(parser, prog=prog)
    root_prog = _validate_prog(root.prog)
    prog_q = _fsq(root_prog)
    specs = _all_specs(root)
    path_func = _fish_func_name(root_prog)

    lines: "list[str]" = []
    lines.append(f"# fish completion for {root_prog}")
    lines.append(f"complete -c {prog_q} -f")
    lines.append("")
    lines.extend(_fish_path_resolver(path_func, specs))

    for cspec in specs:
        cond_args = ["-n", _fsq(_fish_condition(cspec, path_func))]

        for name, sub in cspec.subcommands.items():
            parts = (
                [f"complete -c {prog_q}"] + cond_args + ["-a", _fsq(_fish_word(name))]
            )
            # The one-line help, NOT the fully-qualified prog, as the description.
            description = sub.help or ""
            if description:
                parts.extend(["-d", _fsq(description)])
            lines.append(" ".join(parts))

        for opt in cspec.options:
            long_flags = [f for f in opt.flags if f.startswith("--")]
            # A single-dash MULTI-char flag (e.g. ``-rc``) is an old-style flag:
            # fish's ``-s`` is for a single character only, so use ``-o`` instead.
            short_flags = [
                f
                for f in opt.flags
                if not f.startswith("--")
                and f.startswith("-")
                and len(f.lstrip("-")) == 1
            ]
            old_flags = [
                f
                for f in opt.flags
                if not f.startswith("--")
                and f.startswith("-")
                and len(f.lstrip("-")) > 1
            ]
            parts = [f"complete -c {prog_q}"] + cond_args
            for lf in long_flags:
                parts.extend(["-l", _fsq(lf.lstrip("-"))])
            for sf in short_flags:
                parts.extend(["-s", _fsq(sf.lstrip("-"))])
            for of in old_flags:
                parts.extend(["-o", _fsq(of.lstrip("-"))])
            if opt.takes_value:
                if opt.choices:
                    values = " ".join(_fish_word(c) for c in opt.choices)
                    parts.extend(["-x", "-a", _fsq(values)])
                elif opt.is_path:
                    parts.extend(["-r", "-F"])
                else:
                    # A free-value option: require a value but offer no
                    # candidates of our own; do not also mix in files.
                    parts.append("-x")
            lines.append(" ".join(parts))

        for pos in cspec.positionals:
            if pos.choices:
                values = " ".join(_fish_word(c) for c in pos.choices)
                parts = [f"complete -c {prog_q}"] + cond_args + ["-a", _fsq(values)]
                lines.append(" ".join(parts))
            elif pos.is_path:
                parts = [f"complete -c {prog_q}"] + cond_args + ["-F"]
                lines.append(" ".join(parts))

    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# powershell
# --------------------------------------------------------------------------


def powershell(parser: _argparse.ArgumentParser, prog: "str | None" = None) -> str:
    """Emit a PowerShell completion script for `parser`.

    Registers a ``Register-ArgumentCompleter -Native`` script block that
    reconstructs the (sub)command path from the command elements strictly
    BEFORE the cursor, descending only on words that are real subcommand
    names of the current node (a per-path table, mirroring bash), and
    tracks how many of the current node's own positionals
    have been consumed so only the pending one's choices are offered.

    Every value-taking flag gets an explicit branch -- choices, an empty
    result for a Path flag (native file completion takes over), and an
    empty result for a free-value flag -- and command-path/flag
    comparisons are case-SENSITIVE (``-ceq``/``-ccontains``/``-cmatch``),
    matching argparse instead of PowerShell's default case-insensitivity.
    The inserted completion TEXT is single-quoted (embedded quotes
    doubled) whenever it contains whitespace or a PowerShell metacharacter,
    so a candidate like ``dry run`` or ``$(rm)`` is inserted as one literal
    argument instead of being split or evaluated when the line is run
    -- this is a *different* protection from `_psq`, which only
    keeps the script BODY safe when it is first parsed.
    """
    root = _walk(parser, prog=prog)
    root_prog = _validate_prog(root.prog)
    specs = _all_specs(root)

    lines: "list[str]" = []
    lines.append(f"# PowerShell completion for {root_prog}")
    lines.append(
        f"Register-ArgumentCompleter -Native -CommandName {_psq(root_prog)} "
        f"-ScriptBlock {{"
    )
    lines.append("    param($wordToComplete, $commandAst, $cursorPosition)")
    lines.append("")
    lines.append("    $elements = @($commandAst.CommandElements)")
    lines.append("    $subsByPath = @{}")
    for s in specs:
        if s.subcommands:
            names = ", ".join(_psq(n) for n in s.subcommands)
            lines.append(f"    $subsByPath[{_psq(_cmd_key(s))}] = @({names})")
    lines.append("    $vflagsByPath = @{}")
    for s in specs:
        vflags = _value_flag_names(s)
        if vflags:
            values = ", ".join(_psq(f) for f in vflags)
            lines.append(f"    $vflagsByPath[{_psq(_cmd_key(s))}] = @({values})")
    lines.append("")
    lines.append("    # Reconstruct the (sub)command path from the elements strictly")
    lines.append("    # before the cursor, descending only on real subcommand names")
    lines.append("    # of the current node and skipping a value flag's value.")
    lines.append("    $cmdPath = ''")
    lines.append("    $npos = 0")
    lines.append("    $skip = $false")
    lines.append("    $prev = ''")
    lines.append("    for ($i = 1; $i -lt $elements.Count; $i++) {")
    lines.append("        $el = $elements[$i]")
    lines.append(
        "        if ($el.Extent.StartOffset -ge $cursorPosition -or "
        "$el.Extent.Text -eq $wordToComplete) { continue }"
    )
    lines.append("        $text = $el.Extent.Text")
    lines.append("        $prev = $text")
    lines.append("        if ($skip) { $skip = $false; continue }")
    lines.append("        if ($text -clike '-*') {")
    lines.append("            $vflags = @()")
    lines.append(
        "            if ($vflagsByPath.ContainsKey($cmdPath)) { $vflags = $vflagsByPath[$cmdPath] }"
    )
    lines.append("            if ($vflags -ccontains $text) { $skip = $true }")
    lines.append("            continue")
    lines.append("        }")
    lines.append("        $subs = @()")
    lines.append(
        "        if ($subsByPath.ContainsKey($cmdPath)) { $subs = $subsByPath[$cmdPath] }"
    )
    lines.append("        if ($subs -ccontains $text) {")
    lines.append(
        '            $cmdPath = if ($cmdPath) { "$cmdPath $text" } else { $text }'
    )
    lines.append("            $npos = 0")
    lines.append("        } else {")
    lines.append("            $npos++")
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    lines.append("    $candidates = @()")

    first = True
    for cspec in specs:
        key = _cmd_key(cspec)
        cond = "if" if first else "elseif"
        first = False
        lines.append(f"    {cond} ($cmdPath -ceq {_psq(key)}) {{")

        choice_opts = [o for o in cspec.options if o.takes_value and o.choices]
        other_value_opts = [o for o in cspec.options if o.takes_value and not o.choices]
        if choice_opts or other_value_opts:
            lines.append("        $matched = $false")
            lines.append("        switch -CaseSensitive -Exact ($prev) {")
            for opt in choice_opts:
                values = ", ".join(_psq(c) for c in opt.choices)
                for flag in opt.flags:
                    lines.append(
                        f"            {_psq(flag)} {{ $candidates = @({values}); $matched = $true }}"
                    )
            for opt in other_value_opts:
                # A Path flag: native file completion takes over. A free
                # (non-choice, non-Path) flag: no candidates of our own,
                # never the surrounding flags/subcommand names.
                for flag in opt.flags:
                    lines.append(
                        f"            {_psq(flag)} {{ $candidates = @(); $matched = $true }}"
                    )
            lines.append("        }")
            lines.append("        if (-not $matched) {")
            indent = "            "
        else:
            lines.append("        if ($true) {")
            indent = "            "

        lines.append(f"{indent}if ($wordToComplete -clike '-*') {{")
        flag_names = _flag_names(cspec)
        if flag_names:
            values = ", ".join(_psq(f) for f in flag_names)
            lines.append(f"{indent}    $candidates = @({values})")
        else:
            lines.append(f"{indent}    $candidates = @()")
        lines.append(f"{indent}}} else {{")
        n_pos = len(cspec.positionals)
        if n_pos:
            lines.append(f"{indent}    switch ($npos) {{")
            for idx, pos in enumerate(cspec.positionals):
                lines.append(f"{indent}        {idx} {{")
                if pos.choices:
                    values = ", ".join(_psq(c) for c in pos.choices)
                    lines.append(f"{indent}            $candidates = @({values})")
                else:
                    lines.append(f"{indent}            $candidates = @()")
                lines.append(f"{indent}        }}")
            lines.append(f"{indent}        default {{ $candidates = @() }}")
            lines.append(f"{indent}    }}")
        if cspec.subcommands:
            sub_names = ", ".join(_psq(n) for n in cspec.subcommands)
            lines.append(
                f"{indent}    if ($npos -eq {n_pos}) {{ $candidates = @({sub_names}) }}"
            )
        if not n_pos and not cspec.subcommands:
            lines.append(f"{indent}    $candidates = @()")
        lines.append(f"{indent}}}")
        lines.append("        }")
        lines.append("    }")

    lines.append("")
    lines.append("    $escaped = [regex]::Escape($wordToComplete)")
    lines.append(
        '    $candidates | Where-Object { $_ -cmatch "^$escaped" } '
        "| Sort-Object -Unique -CaseSensitive | ForEach-Object {"
    )
    lines.append("        $text = $_")
    lines.append("        if ($text -cmatch '[\\s`\"''$();|&<>{}]') {")
    lines.append('            $text = "\'" + ($text -replace "\'", "\'\'") + "\'"')
    lines.append("        }")
    lines.append(
        "        [System.Management.Automation.CompletionResult]::new("
        "$text, $_, 'ParameterValue', $_)"
    )
    lines.append("    }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)
