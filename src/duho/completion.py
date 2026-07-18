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

__all__ = ["CompletionOption", "CompletionPositional", "CompletionSpec", "bash", "zsh", "fish"]


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
        choices = subparsers_action.choices or {}
        for name, subparser in choices.items():
            spec.subcommands[name] = _walk(subparser, prog=f"{spec.prog} {name}")

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
    root_prog = root.prog
    func = _func_name(root_prog)

    lines: "list[str]" = []
    lines.append(f"# bash completion for {root_prog}")
    lines.append(f"_{func}() {{")
    lines.append('    local cur prev words cword')
    lines.append('    COMPREPLY=()')
    lines.append('    cur="${COMP_WORDS[COMP_CWORD]}"')
    lines.append('    prev="${COMP_WORDS[COMP_CWORD-1]}"')
    lines.append('')
    lines.append('    # Walk COMP_WORDS to find which (sub)command we are in.')
    lines.append('    local cmd_path=""')
    lines.append('    local i=1')
    lines.append('    while [ $i -lt $COMP_CWORD ]; do')
    lines.append('        case "${COMP_WORDS[i]}" in')
    lines.append('            -*) ;;')
    lines.append('            *) cmd_path="${cmd_path} ${COMP_WORDS[i]}" ;;')
    lines.append('        esac')
    lines.append('        i=$((i + 1))')
    lines.append('    done')
    lines.append('    cmd_path="$(echo "$cmd_path" | xargs)"')
    lines.append('')

    for spec in _all_specs(root):
        opts = " ".join(sorted({f for opt in spec.options for f in opt.flags}))
        subcmds = " ".join(sorted(spec.subcommands))
        key = spec.prog[len(root_prog):].strip()
        lines.append(f'    if [ "$cmd_path" = "{key}" ]; then')

        # prev-based value completion (choices/paths) for this command.
        value_opts = [o for o in spec.options if o.takes_value]
        if value_opts:
            lines.append('        case "$prev" in')
            for opt in value_opts:
                flag_pattern = "|".join(opt.flags)
                if opt.choices:
                    words = " ".join(opt.choices)
                    lines.append(f'            {flag_pattern})')
                    lines.append(f'                COMPREPLY=( $(compgen -W "{words}" -- "$cur") )')
                    lines.append('                return 0 ;;')
                elif opt.is_path:
                    lines.append(f'            {flag_pattern})')
                    lines.append('                COMPREPLY=( $(compgen -f -- "$cur") )')
                    lines.append('                return 0 ;;')
            lines.append('        esac')

        candidates = list(opts.split()) if opts else []
        if subcmds:
            candidates.extend(subcmds.split())
        for pos in spec.positionals:
            if pos.choices:
                candidates.extend(pos.choices)

        if candidates:
            words = " ".join(candidates)
            lines.append(f'        COMPREPLY=( $(compgen -W "{words}" -- "$cur") )')
        else:
            lines.append('        COMPREPLY=( $(compgen -f -- "$cur") )')
        lines.append('        return 0')
        lines.append('    fi')

    lines.append('}')
    lines.append(f'complete -F _{func} {root_prog}')
    lines.append('')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# zsh
# --------------------------------------------------------------------------


def _zsh_arguments_block(spec: CompletionSpec, root_prog: str, indent: str = "    ") -> "list[str]":
    lines: "list[str]" = []
    lines.append(f"{indent}local -a args")
    lines.append(f"{indent}args=(")
    for opt in spec.options:
        flag_list = "|".join(opt.flags)
        flags = flag_list if len(opt.flags) == 1 else f"'{{{flag_list}}}'"
        if opt.takes_value:
            if opt.choices:
                values = " ".join(opt.choices)
                lines.append(f"{indent}    {flags}'[option]:value:({values})'")
            elif opt.is_path:
                lines.append(f"{indent}    {flags}'[option]:value:_files'")
            else:
                lines.append(f"{indent}    {flags}'[option]:value:'")
        else:
            lines.append(f"{indent}    {flags}'[option]'")
    if spec.subcommands:
        names = " ".join(spec.subcommands)
        lines.append(f"{indent}    '1:command:({names})'")
        lines.append(f"{indent}    '*::arg:->args'")
    for pos in spec.positionals:
        if pos.choices:
            values = " ".join(pos.choices)
            lines.append(f"{indent}    '{pos.name}:{pos.name}:({values})'")
        elif pos.is_path:
            lines.append(f"{indent}    '{pos.name}:{pos.name}:_files'")
        else:
            lines.append(f"{indent}    '{pos.name}:{pos.name}:'")
    lines.append(f"{indent})")
    lines.append(f"{indent}_arguments -s $args")
    return lines


def zsh(parser: _argparse.ArgumentParser, prog: "str | None" = None) -> str:
    """Emit a `#compdef`-style zsh completion script for `parser`.

    Uses `_arguments`/`_describe`: subcommand names and option choices are
    rendered as `(a b c)` value lists; Path-typed args delegate to `_files`.
    """
    root = _walk(parser, prog=prog)
    root_prog = root.prog
    func = _func_name(root_prog)

    lines: "list[str]" = []
    lines.append(f"#compdef {root_prog}")
    lines.append("")
    lines.append(f"_{func}() {{")
    lines.append("    local -a subcmds")
    lines.append("    local context state state_descr line")
    lines.append("    typeset -A opt_args")
    lines.append("")
    lines.append("    local cmd_path=\"${words[2,CURRENT-1]}\"")
    lines.append("")

    for spec in _all_specs(root):
        key = spec.prog[len(root_prog):].strip()
        lines.append(f'    if [[ "$cmd_path" == "{key}" ]]; then')
        lines.extend(_zsh_arguments_block(spec, root_prog, indent="        "))
        if spec.subcommands:
            lines.append(f'        _describe "{spec.prog} subcommand" subcmds')
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
    root_prog = root.prog

    lines: "list[str]" = []
    lines.append(f"# fish completion for {root_prog}")
    lines.append(f"complete -c {root_prog} -f")
    lines.append("")

    for spec in _all_specs(root):
        cond = _fish_condition(spec, root)
        cond_args = ["-n", f"'{cond}'"] if cond else []

        for name, sub in spec.subcommands.items():
            parts = [f"complete -c {root_prog}"] + cond_args + ["-a", name]
            docstring = sub.prog
            parts.extend(["-d", f"'{docstring}'"])
            lines.append(" ".join(parts))

        for opt in spec.options:
            long_flags = [f for f in opt.flags if f.startswith("--")]
            short_flags = [f for f in opt.flags if not f.startswith("--") and f.startswith("-")]
            parts = [f"complete -c {root_prog}"] + cond_args
            for lf in long_flags:
                parts.extend(["-l", lf.lstrip("-")])
            for sf in short_flags:
                parts.extend(["-s", sf.lstrip("-")])
            if opt.takes_value:
                parts.append("-r")
                if opt.choices:
                    values = " ".join(opt.choices)
                    parts.extend(["-a", f"'{values}'"])
                elif opt.is_path:
                    parts.append("-F")
            lines.append(" ".join(parts))

        for pos in spec.positionals:
            if pos.choices:
                values = " ".join(pos.choices)
                parts = [f"complete -c {root_prog}"] + cond_args + ["-a", f"'{values}'"]
                lines.append(" ".join(parts))
            elif pos.is_path:
                parts = [f"complete -c {root_prog}"] + cond_args + ["-F"]
                lines.append(" ".join(parts))

    lines.append("")
    return "\n".join(lines)
