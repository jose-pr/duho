from __future__ import annotations

import argparse as _argparse
import hashlib as _hashlib
import typing as _ty

from ._quoting import _fish_word, _fsq, _validate_prog
from ._spec import (
    CompletionSpec,
    _all_specs,
    _cmd_key,
    _func_name,
    _value_flag_names,
    _walk,
)

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


def _fish_path_resolver(path_func: str, specs: list[CompletionSpec]) -> list[str]:
    """Emit a fish function that resolves the (sub)command path typed so far.

    `__fish_seen_subcommand_from` ignores position and depth, so a name reused
    at a deeper level (root `run`, nested `db run`) would leak the root node's
    flags into the nested one. Walking the command line and gating on exact
    path equality removes that. `commandline -opc` is already tokenized and
    dequoted, so no quote-stripping is needed, unlike bash and PowerShell.
    """
    lines: list[str] = []
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


def fish(parser: _argparse.ArgumentParser, prog: _ty.Optional[str] = None) -> str:
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

    lines: list[str] = []
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
