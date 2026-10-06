from __future__ import annotations

import argparse as _argparse
import hashlib as _hashlib

from ._quoting import _bash_wordlist, _bashq, _validate_prog
from ._spec import (
    _all_specs,
    _cmd_key,
    _flag_names,
    _func_name,
    _value_flag_names,
    _walk,
)


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


def bash(parser: _argparse.ArgumentParser, prog: str | None = None) -> str:
    """Emit a self-contained bash completion script for `parser`.

    Descends the command line only on a word that is BOTH a real subcommand
    name of the CURRENT node AND typed once that node's own positionals are
    already satisfied -- argparse itself consumes a node's own positionals
    before ever treating a word as its subparsers dispatch value, so a
    positional whose `choices` happen to include a real subcommand name is
    never mistaken for one; every other bare word counts as one of that
    node's own positionals, tracked by position so only the pending
    positional's own candidates (its `choices` via `compgen -W`, or
    `compgen -f` for a Path positional) are offered, not every positional's
    at once. A value-taking flag's value is skipped the same way, including
    the split `--opt = value` form bash produces for `--opt=value`; the
    value-flag set is resolved per command path, not merged globally.
    Every value-taking flag -- choice, Path, or free -- gets its own
    `$prev` arm. `COMPREPLY` is always filled by a `while IFS= read -r` loop
    over a process-substitution `compgen` call (not `mapfile`, which bash
    3.2 -- still macOS's `/bin/bash` -- does not have), never `$(...)` splicing, so results
    are never word-split or glob-expanded a second time. Registered
    with `-o bashdefault -o default -o filenames` so bash's native filename
    completion applies whenever nothing above matches, and Path
    positionals/options get properly escaped/slashed directory names. Every
    candidate word list (`compgen -W`) is itself escaped character-by-character
    against a conservative safe set (see `_bash_wordlist`) so `compgen`'s own
    SECOND, dynamic re-evaluation of that argument cannot run command or
    process substitution, expand a glob, or split on an embedded space.
    """
    root = _walk(parser, prog=prog)
    root_prog = _validate_prog(root.prog)
    func = _bash_func_name(root_prog)
    specs = _all_specs(root)

    lines: list[str] = []
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
    lines.append("    # node AND only once that node's own positionals are already")
    lines.append("    # satisfied (argparse consumes a node's own positionals before")
    lines.append("    # ever treating a word as its subparsers dispatch value, so a")
    lines.append("    # positional whose choices happen to include a real subcommand")
    lines.append(
        "    # name must not be mistaken for one); skip the value that follows"
    )
    lines.append("    # a value-taking flag of the current node (including the split")
    lines.append("    # `--opt = value` form), and count every other bare word as one")
    lines.append("    # of the current node's own positionals.")
    lines.append('    local cmd_path=""')
    lines.append("    local npos=0")
    lines.append("    local i=1")
    lines.append("    local skip=0")
    lines.append("    local w is_vflag is_sub own_pos")
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
    lines.append("                    own_pos=0")
    lines.append('                    case "$cmd_path" in')
    for s in specs:
        if not s.subcommands:
            continue
        lines.append(
            f"                        {_bashq(_cmd_key(s))}) own_pos={len(s.positionals)} ;;"
        )
    lines.append("                    esac")
    lines.append("                    if [ $npos -ge $own_pos ]; then")
    lines.append('                        case "$cmd_path" in')
    for s in specs:
        if not s.subcommands:
            continue
        pattern = "|".join(_bashq(n) for n in s.subcommands)
        lines.append(f"                            {_bashq(_cmd_key(s))})")
        lines.append('                                case "$w" in')
        lines.append(f"                                    {pattern}) is_sub=1 ;;")
        lines.append("                                esac")
        lines.append("                                ;;")
    lines.append("                        esac")
    lines.append("                    fi")
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
                        f'                COMPREPLY=(); while IFS= read -r w; do COMPREPLY+=("$w"); done < <(compgen -W {words} -- "$cur")'
                    )
                elif opt.is_path:
                    lines.append(
                        '                COMPREPLY=(); while IFS= read -r w; do COMPREPLY+=("$w"); done < <(compgen -f -- "$cur")'
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
                f'                COMPREPLY=(); while IFS= read -r w; do COMPREPLY+=("$w"); done < <(compgen -W {words} -- "$cur")'
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
                        f'                COMPREPLY=(); while IFS= read -r w; do COMPREPLY+=("$w"); done < <(compgen -W {words} -- "$cur")'
                    )
                elif pos.is_path:
                    lines.append(
                        '                COMPREPLY=(); while IFS= read -r w; do COMPREPLY+=("$w"); done < <(compgen -f -- "$cur")'
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
                f'            COMPREPLY=(); while IFS= read -r w; do COMPREPLY+=("$w"); done < <(compgen -W {words} -- "$cur")'
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
