"""Shell completion script generation (bash/zsh/fish).

**Decision (do not revisit): STATIC script generation** -- these functions
emit a self-contained completion script the user installs once, NOT a
dynamic argcomplete-style hook that re-invokes the program on every Tab.
Zero runtime dependency, zero per-invocation cost: a core differentiator
vs. argcomplete.

All three emitters (`bash`, `zsh`, `fish`) share one parser-tree walk
(`_walk`) that turns a *built* `argparse.ArgumentParser` into a plain,
shell-agnostic `CompletionSpec`. Only the emitters know shell syntax.

Completion data is read off the built parser's private attrs
(`parser._actions`, `parser._subparsers`) -- the same internal contract
`parsers.py` already relies on elsewhere in this codebase.
"""

import argparse as _argparse
import dataclasses as _dc
import pathlib as _pathlib
import shlex as _shlex

__all__ = ["CompletionOption", "CompletionPositional", "CompletionSpec", "bash", "zsh", "fish"]


def _bashq(value: object) -> str:
    """POSIX-shell-quote a single value used as a direct bash argument."""
    return _shlex.quote(str(value))


def _bash_wordlist(values: "list") -> str:
    """Build a safe ``compgen -W`` word-list argument from ``values``.

    ``compgen -W`` *expands* its word list (command substitution, parameter
    expansion, ...), so a hostile choice like ``$(rm -rf ~)`` would RUN at
    Tab-press if merely quoted. Neutralise the expansion triggers by
    backslash-escaping ``\\``/``$``/`` ` `` in each value, then single-quote the
    whole list (embedded single quotes as ``'\\''``) so the escapes survive to
    compgen literally (M2).
    """
    escaped: "list[str]" = []
    for value in values:
        s = str(value)
        for ch in ("\\", "$", "`"):
            s = s.replace(ch, "\\" + ch)
        escaped.append(s)
    joined = " ".join(escaped)
    return "'" + joined.replace("'", "'\\''") + "'"


def _sq(value: object) -> str:
    """Single-quote a value for a zsh/fish single-quoted context.

    An embedded single quote is closed, escaped, and reopened (``'\\''``) so a
    choice like ``it's`` cannot break out of the surrounding quotes or inject
    shell code (M2).
    """
    return "'" + str(value).replace("'", "'\\''") + "'"


def _validate_prog(prog: str) -> str:
    """Validate a program name used unquoted as a completion function target.

    ``prog`` names a shell function (``_<prog>``) and the completed command; a
    value with whitespace or shell metacharacters would corrupt the generated
    script, so it is rejected with a clear error (M2). A normal prog (letters,
    digits, ``_``/``-``/``.``) passes untouched.
    """
    if prog != prog.strip() or any(c.isspace() for c in prog):
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


@_dc.dataclass
class CompletionSpec:
    """Shell-agnostic view of a single (sub)parser and its subcommand tree."""

    prog: str
    options: "list[CompletionOption]" = _dc.field(default_factory=list)
    positionals: "list[CompletionPositional]" = _dc.field(default_factory=list)
    subcommands: "dict[str, CompletionSpec]" = _dc.field(default_factory=dict)
    #: One-line help for THIS (sub)command, used as the fish ``-d`` description.
    help: str = ""


def _is_path_type(action: _argparse.Action) -> bool:
    ty = getattr(action, "type", None)
    return isinstance(ty, type) and issubclass(ty, _pathlib.Path)


def _choices_tuple(action: _argparse.Action) -> "tuple[str, ...] | None":
    choices = getattr(action, "choices", None)
    if not choices:
        return None
    return tuple(str(c) for c in choices)


def _takes_value(action: _argparse.Action) -> bool:
    # nargs == 0 means a flag-style action (store_true/store_false/help/
    # version/store_const/etc.) that never consumes a value.
    return action.nargs != 0


def _walk(parser: _argparse.ArgumentParser, prog: "str | None" = None) -> CompletionSpec:
    """Recursively turn a built ArgumentParser into a CompletionSpec.

    Pure data, no shell strings -- shared by all three emitters below.
    """
    spec = CompletionSpec(prog=prog or parser.prog)

    subparsers_action = None
    for action in parser._actions:
        if isinstance(action, _argparse._SubParsersAction):
            subparsers_action = action
            continue
        if not action.option_strings:
            # Positional argument.
            spec.positionals.append(
                CompletionPositional(
                    name=action.dest,
                    choices=_choices_tuple(action),
                    is_path=_is_path_type(action),
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
        # not on the subparser -- capture it here for the fish `-d` description.
        help_by_name = {
            getattr(a, "dest", None): (getattr(a, "help", None) or "")
            for a in getattr(subparsers_action, "_choices_actions", [])
        }
        choices = subparsers_action.choices or {}
        for name, subparser in choices.items():
            sub_spec = _walk(subparser, prog=f"{spec.prog} {name}")
            sub_spec.help = help_by_name.get(name, "")
            spec.subcommands[name] = sub_spec

    return spec


def _all_specs(spec: CompletionSpec) -> "list[CompletionSpec]":
    """Flatten a spec tree into a list (root first, depth-first)."""
    result = [spec]
    for sub in spec.subcommands.values():
        result.extend(_all_specs(sub))
    return result


def _func_name(prog: str) -> str:
    """Turn a (possibly multi-word, sub-command-qualified) prog into a
    shell-safe identifier fragment."""
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in prog)


# --------------------------------------------------------------------------
# bash
# --------------------------------------------------------------------------


def bash(parser: _argparse.ArgumentParser, prog: "str | None" = None) -> str:
    """Emit a self-contained bash completion script for `parser`.

    Registers ``complete -F _<prog> <prog>``. Choices use `compgen -W`,
    Path-typed args fall back to `compgen -f`/`-d` (native file/dir
    completion), non-Path/non-choice args get no candidates (bash's default
    filename completion still applies).
    """
    root = _walk(parser, prog=prog)
    root_prog = _validate_prog(root.prog)
    func = _func_name(root_prog)

    # Flags that CONSUME a value: when one appears in COMP_WORDS its following
    # word is that value, not a subcommand -- skip it when reconstructing the
    # command path, or `myapp --env prod deploy` builds cmd_path "prod deploy"
    # and no completion matches (M8).
    value_flags = sorted(
        {f for spec in _all_specs(root) for opt in spec.options if opt.takes_value for f in opt.flags}
    )
    value_flags_pat = " ".join(value_flags)

    lines: "list[str]" = []
    lines.append(f"# bash completion for {root_prog}")
    lines.append(f"_{func}() {{")
    lines.append('    local cur prev words cword')
    lines.append('    COMPREPLY=()')
    lines.append('    cur="${COMP_WORDS[COMP_CWORD]}"')
    lines.append('    prev="${COMP_WORDS[COMP_CWORD-1]}"')
    lines.append('')
    lines.append('    # Walk COMP_WORDS to find which (sub)command we are in,')
    lines.append('    # skipping option flags AND the value that follows a')
    lines.append('    # value-taking flag.')
    lines.append(f'    local value_flags=" {value_flags_pat} "')
    lines.append('    local cmd_path=""')
    lines.append('    local i=1')
    lines.append('    local skip_next=0')
    lines.append('    while [ $i -lt $COMP_CWORD ]; do')
    lines.append('        local w="${COMP_WORDS[i]}"')
    lines.append('        if [ $skip_next -eq 1 ]; then')
    lines.append('            skip_next=0')
    lines.append('        else')
    lines.append('            case "$w" in')
    lines.append('                -*)')
    lines.append('                    case "$value_flags" in')
    lines.append('                        *" $w "*) skip_next=1 ;;')
    lines.append('                    esac')
    lines.append('                    ;;')
    lines.append('                *) cmd_path="${cmd_path} $w" ;;')
    lines.append('            esac')
    lines.append('        fi')
    lines.append('        i=$((i + 1))')
    lines.append('    done')
    lines.append('    cmd_path="$(echo "$cmd_path" | xargs)"')
    lines.append('')

    for spec in _all_specs(root):
        opts = sorted({f for opt in spec.options for f in opt.flags})
        key = spec.prog[len(root_prog):].strip()
        lines.append(f'    if [ "$cmd_path" = {_bashq(key)} ]; then')

        # prev-based value completion (choices/paths) for this command.
        value_opts = [o for o in spec.options if o.takes_value]
        if value_opts:
            lines.append('        case "$prev" in')
            for opt in value_opts:
                flag_pattern = "|".join(opt.flags)
                if opt.choices:
                    words = _bash_wordlist(list(opt.choices))
                    lines.append(f'            {flag_pattern})')
                    lines.append(f'                COMPREPLY=( $(compgen -W {words} -- "$cur") )')
                    lines.append('                return 0 ;;')
                elif opt.is_path:
                    lines.append(f'            {flag_pattern})')
                    lines.append('                COMPREPLY=( $(compgen -f -- "$cur") )')
                    lines.append('                return 0 ;;')
            lines.append('        esac')

        candidates = list(opts)
        candidates.extend(sorted(spec.subcommands))
        for pos in spec.positionals:
            if pos.choices:
                candidates.extend(pos.choices)

        if candidates:
            words = _bash_wordlist(candidates)
            lines.append(f'        COMPREPLY=( $(compgen -W {words} -- "$cur") )')
        else:
            lines.append('        COMPREPLY=( $(compgen -f -- "$cur") )')
        lines.append('        return 0')
        lines.append('    fi')

    lines.append('}')
    lines.append(f'complete -F _{func} {_bashq(root_prog)}')
    lines.append('')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# zsh
# --------------------------------------------------------------------------


def _zsh_value_part(opt: "CompletionOption") -> str:
    """The ``:message:action`` tail of a zsh optspec for a value-taking option."""
    if opt.choices:
        values = " ".join(str(c) for c in opt.choices)
        return f":value:({values})"
    if opt.is_path:
        return ":value:_files"
    return ":value:"


def _zsh_optspec(opt: "CompletionOption") -> str:
    """Build one zsh ``_arguments`` optspec for ``opt``.

    A single-flag option is ``<flag>'[desc]...'``; a multi-flag option uses the
    exclusion-list + brace-expansion form ``'(-v --verbose)'{-v,--verbose}'[desc]...'``
    -- the previous ``'{-v|--verbose}'`` quoted-pipe brace was invalid zsh (C12).
    Every interpolated part is single-quoted with embedded-quote escaping (M2).
    """
    tail_inner = "[option]"
    if opt.takes_value:
        tail_inner += _zsh_value_part(opt)
    tail = _sq(tail_inner)
    if len(opt.flags) == 1:
        return opt.flags[0] + tail
    exclusion = _sq("(" + " ".join(opt.flags) + ")")
    brace = "{" + ",".join(opt.flags) + "}"
    return exclusion + brace + tail


def _zsh_arguments_block(spec: CompletionSpec, indent: str = "    ") -> "list[str]":
    lines: "list[str]" = []
    lines.append(f"{indent}local -a args")
    lines.append(f"{indent}args=(")
    for opt in spec.options:
        lines.append(f"{indent}    {_zsh_optspec(opt)}")
    if spec.subcommands:
        names = " ".join(str(n) for n in spec.subcommands)
        lines.append(f"{indent}    {_sq('1:command:(' + names + ')')}")
        lines.append(f"{indent}    {_sq('*::arg:->args')}")
    for pos in spec.positionals:
        if pos.choices:
            values = " ".join(str(c) for c in pos.choices)
            spec_str = f"{pos.name}:{pos.name}:({values})"
        elif pos.is_path:
            spec_str = f"{pos.name}:{pos.name}:_files"
        else:
            spec_str = f"{pos.name}:{pos.name}:"
        lines.append(f"{indent}    {_sq(spec_str)}")
    lines.append(f"{indent})")
    lines.append(f"{indent}_arguments -s $args")
    return lines


def zsh(parser: _argparse.ArgumentParser, prog: "str | None" = None) -> str:
    """Emit a `#compdef`-style zsh completion script for `parser`.

    Uses `_arguments`: subcommand names and option choices are rendered as
    `(a b c)` value lists; Path-typed args delegate to `_files`. The command path
    is rebuilt from the non-option words only, so a flag before the cursor no
    longer breaks completion (C12).
    """
    root = _walk(parser, prog=prog)
    root_prog = _validate_prog(root.prog)
    func = _func_name(root_prog)

    lines: "list[str]" = []
    lines.append(f"#compdef {root_prog}")
    lines.append("")
    lines.append(f"_{func}() {{")
    lines.append("    local context state state_descr line")
    lines.append("    typeset -A opt_args")
    lines.append("")
    lines.append("    # Reconstruct the (sub)command path from non-option words.")
    lines.append("    local -a path_words")
    lines.append("    integer idx=2")
    lines.append("    while (( idx < CURRENT )); do")
    lines.append('        if [[ "${words[idx]}" != -* ]]; then')
    lines.append('            path_words+=("${words[idx]}")')
    lines.append("        fi")
    lines.append("        (( idx++ ))")
    lines.append("    done")
    lines.append('    local cmd_path="${(j: :)path_words}"')
    lines.append("")

    for spec in _all_specs(root):
        key = spec.prog[len(root_prog):].strip()
        lines.append(f'    if [[ "$cmd_path" == {_sq(key)} ]]; then')
        lines.extend(_zsh_arguments_block(spec, indent="        "))
        lines.append("        return")
        lines.append("    fi")

    lines.append("}")
    lines.append("")
    lines.append(f"_{func} \"$@\"")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# fish
# --------------------------------------------------------------------------


def _fish_condition(spec: CompletionSpec, root: CompletionSpec) -> "str | None":
    """Build a `__fish_seen_subcommand_from` condition chain locating `spec`
    within the tree rooted at `root`, or None for the root command itself."""
    key = spec.prog[len(root.prog):].strip()
    if not key:
        return None
    parts = key.split()
    conds = [f"__fish_seen_subcommand_from {part}" for part in parts]
    return " and ".join(conds)


def fish(parser: _argparse.ArgumentParser, prog: "str | None" = None) -> str:
    """Emit a fish completion script (`complete -c <prog> ...` lines) for `parser`.

    Choices become `-a`, value-taking options get `-r` (require an
    argument), Path-typed options additionally get `-F` to enable fish's
    native file completion; subcommand-scoped rules are gated on
    `__fish_seen_subcommand_from`.
    """
    root = _walk(parser, prog=prog)
    root_prog = _validate_prog(root.prog)
    prog_q = _sq(root_prog)

    lines: "list[str]" = []
    lines.append(f"# fish completion for {root_prog}")
    lines.append(f"complete -c {prog_q} -f")
    lines.append("")

    for spec in _all_specs(root):
        cond = _fish_condition(spec, root)
        cond_args = ["-n", _sq(cond)] if cond else []

        for name, sub in spec.subcommands.items():
            parts = [f"complete -c {prog_q}"] + cond_args + ["-a", _sq(name)]
            # The one-line help, NOT the fully-qualified prog, as the description.
            description = sub.help or ""
            if description:
                parts.extend(["-d", _sq(description)])
            lines.append(" ".join(parts))

        for opt in spec.options:
            long_flags = [f for f in opt.flags if f.startswith("--")]
            # A single-dash MULTI-char flag (e.g. ``-rc``) is an old-style flag:
            # fish's ``-s`` is for a single character only, so use ``-o`` (M2/fish).
            short_flags = [
                f for f in opt.flags
                if not f.startswith("--") and f.startswith("-") and len(f.lstrip("-")) == 1
            ]
            old_flags = [
                f for f in opt.flags
                if not f.startswith("--") and f.startswith("-") and len(f.lstrip("-")) > 1
            ]
            parts = [f"complete -c {prog_q}"] + cond_args
            for lf in long_flags:
                parts.extend(["-l", _sq(lf.lstrip("-"))])
            for sf in short_flags:
                parts.extend(["-s", _sq(sf.lstrip("-"))])
            for of in old_flags:
                parts.extend(["-o", _sq(of.lstrip("-"))])
            if opt.takes_value:
                parts.append("-r")
                if opt.choices:
                    values = " ".join(str(c) for c in opt.choices)
                    parts.extend(["-a", _sq(values)])
                elif opt.is_path:
                    parts.append("-F")
            lines.append(" ".join(parts))

        for pos in spec.positionals:
            if pos.choices:
                values = " ".join(str(c) for c in pos.choices)
                parts = [f"complete -c {prog_q}"] + cond_args + ["-a", _sq(values)]
                lines.append(" ".join(parts))
            elif pos.is_path:
                parts = [f"complete -c {prog_q}"] + cond_args + ["-F"]
                lines.append(" ".join(parts))

    lines.append("")
    return "\n".join(lines)
