from __future__ import annotations

import argparse as _argparse
import hashlib as _hashlib
import typing as _ty

from ._quoting import _sq, _validate_prog, _zsh_word
from ._spec import CompletionOption, CompletionPositional, _all_specs, _func_name, _walk

# --------------------------------------------------------------------------
# zsh
# --------------------------------------------------------------------------


def _zsh_value_part(opt: CompletionOption) -> str:
    """The ``:message:action`` tail of a zsh optspec for a value-taking option."""
    if opt.choices:
        values = " ".join(_zsh_word(c) for c in opt.choices)
        return f":value:({values})"
    if opt.is_path:
        return ":value:_files"
    return ":value:"


def _zsh_optname(flag: str) -> str:
    """Escape ``flag`` for ``_arguments``' own parse of an option spec, which
    ends the name at an unescaped ``:`` or ``[`` and splits an exclusion list
    on whitespace."""
    return "".join("\\" + c if c in "\\:[] \t" else c for c in flag)


def _zsh_optspec(opt: CompletionOption) -> str:
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
    flags = [_zsh_optname(f) for f in opt.flags]
    if len(flags) == 1:
        return _sq(flags[0]) + tail
    exclusion = _sq("(" + " ".join(flags) + ")")
    brace = "{" + ",".join(_sq(f) for f in flags) + "}"
    return exclusion + brace + tail


def _zsh_pos_spec(n: int, pos: CompletionPositional) -> str:
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


def _zsh_seg(raw: str) -> str:
    """A collision-resistant zsh function-name segment for one path
    component.

    `_func_name` folds any non-identifier character to `_`, so two distinct
    sibling names that differ only in punctuation (`a-b`/`a_b`) sanitise to
    the SAME segment -- the second definition then silently overwrites the
    first zsh function, and every completion for the first name silently
    dispatches into the second's instead. Append a short content hash of the
    RAW name, the same fix `_bash_func_name` already applies to the root
    function name.
    """
    safe = _func_name(raw)
    digest = _hashlib.sha1(raw.encode("utf-8", "surrogateescape")).hexdigest()[:6]
    return f"{safe}_{digest}"


def _zsh_root_func_name(root_prog: str) -> str:
    """A collision-resistant zsh ROOT function-name base for ``root_prog``,
    mirroring `_bash_func_name`/`_fish_func_name`'s hashing.

    Every nested funcid is built as ``_<this>__<seg>...`` (see
    `_zsh_funcid`), so leaving this base unhashed meant two progs that
    sanitise to the same identifier (`my-app`/`my.app`/`my_app` -> `my_app`)
    still collided on EVERY function the emitter defines for them, not just
    the root one -- `_zsh_seg`'s per-segment hash only protects a nested
    node from colliding with a SIBLING under the same (already-shared) root.
    """
    safe = _func_name(root_prog)
    digest = _hashlib.sha1(root_prog.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    return f"{safe}_{digest}"


def _zsh_funcid(func: str, path: tuple[str, ...]) -> str:
    """The zsh function name for the node at ``path``: ``_<func>`` for the
    root, ``_<func>__<seg1>__<seg2>...`` for a nested node."""
    if not path:
        return f"_{func}"
    suffix = "__".join(_zsh_seg(p) for p in path)
    return f"_{func}__{suffix}"


def zsh(parser: _argparse.ArgumentParser, prog: _ty.Optional[str] = None) -> str:
    """Emit a `#compdef`-style zsh completion script for `parser`.

    Standard zsh subcommand dispatch: one function per (sub)command node. A
    node with subcommands calls `_arguments -C -s` with a single `*::arg:->args`
    rest spec covering EVERYTHING after its own options -- never separate
    numbered specs for its own positionals or the command name. Sharing one
    `_arguments -C` call between a plain numbered positional and `*::` makes
    zsh's own bookkeeping of "which word is which" ambiguous (`$line` cycles
    between several candidate splits instead of settling on one), so a node
    with a positional of its own ahead of a subcommand table never reached
    that subcommand at all. Folding both the node's own positionals AND the
    command word into `*::arg:->args` keeps `$line` a single, deterministic
    source of truth: `$line`'s last element is always the word currently
    being completed (even when empty), so `$#line - 1` is the number of this
    node's own words already typed in full -- fewer than its positional
    count still means completing one of ITS OWN positionals, exactly that
    many means completing the command word itself, and more means dispatch:
    `$line[<n_pos + 1>]` is the (already-typed) subcommand name.  Because the
    real `_arguments` engine parses options for us, a value-taking flag
    before the cursor never breaks this count. `${(Q)...}` strips any
    quoting the user themselves typed around the subcommand name before
    comparing it to our own unquoted case labels -- otherwise a subcommand
    name that needed quoting (spaces, a shell metacharacter) could never
    dispatch, since `$line` preserves the user's raw typed form. Every
    choice and positional message is escaped for zsh's SECOND (dynamic)
    evaluation via `_zsh_word` before being wrapped in `_sq` for the first:
    a value containing `$(...)`, `;`, or a colon cannot run code or
    break the spec at Tab-time. The root function name itself is hashed
    (`_zsh_root_func_name`), matching `_bash_func_name`/`_fish_func_name`, so
    two progs that sanitise to the same identifier (`my-app`/`my.app`) never
    collide -- every nested function name is built on top of this root name.
    """
    root = _walk(parser, prog=prog)
    root_prog = _validate_prog(root.prog)
    func = _zsh_root_func_name(root_prog)

    lines: list[str] = []
    lines.append(f"#compdef {root_prog}")
    lines.append("")

    for cspec in _all_specs(root):
        funcid = _zsh_funcid(func, cspec.path)
        lines.append(f"{funcid} () {{")

        args_items: list[str] = [_zsh_optspec(opt) for opt in cspec.options]
        n_pos = len(cspec.positionals)

        if cspec.subcommands:
            lines.append('    local curcontext="$curcontext" state state_descr line')
            lines.append("    typeset -A opt_args")
            args_items.append(_sq("*::arg:->args"))
            arguments_flags = "-C -s"
        else:
            n = 0
            for pos in cspec.positionals:
                n += 1
                args_items.append(_sq(_zsh_pos_spec(n, pos)))
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
            lines.append("            case $(( ${#line} - 1 )) in")
            for idx, pos in enumerate(cspec.positionals):
                lines.append(f"                {idx})")
                if pos.choices:
                    # Each candidate is one literal shell word (`compadd` gets only
                    # the static parse), so `_sq` alone suffices, without the
                    # `_zsh_word` an `_arguments` spec string needs.
                    values = " ".join(_sq(c) for c in pos.choices)
                    lines.append(f"                    compadd -- {values}")
                elif pos.is_path:
                    lines.append("                    _files")
                lines.append("                    ;;")
            lines.append(f"                {n_pos})")
            names = " ".join(_sq(name) for name in cspec.subcommands)
            lines.append(f"                    compadd -- {names}")
            lines.append("                    ;;")
            lines.append("                *)")
            # `_arguments -C` republishes `$words`/`$CURRENT` only relative to
            # `*::`, not past this node's `n_pos` own positionals: reset both
            # here to start at the subcommand name.
            lines.append("                    local words CURRENT")
            # `(@)` keeps an array slice, including a trailing empty element for
            # the word being completed; a plain slice would drop it.
            lines.append(f'                    words=("${{(@)line[{n_pos + 1},-1]}}")')
            lines.append("                    CURRENT=${#words}")
            lines.append(f"                    case ${{(Q)line[{n_pos + 1}]}} in")
            for name, sub in cspec.subcommands.items():
                subfuncid = _zsh_funcid(func, sub.path)
                # A quoted case label is matched literally (no glob
                # interpretation of a hostile name) -- reuse `_sq`.
                lines.append(f"                        {_sq(name)})")
                lines.append(f"                            {subfuncid}")
                lines.append("                            ;;")
            lines.append("                    esac")
            lines.append("                    ;;")
            lines.append("            esac")
            lines.append("            ;;")
            lines.append("    esac")

        lines.append("}")
        lines.append("")

    lines.append(f'{_zsh_funcid(func, ())} "$@"')
    lines.append("")
    return "\n".join(lines)
