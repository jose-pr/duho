from __future__ import annotations

import shlex as _shlex


def _bashq(value: object) -> str:
    """POSIX-shell-quote a single value used as a direct bash argument."""
    return _shlex.quote(str(value))


def _bash_wordlist(values: list) -> str:
    """Build a safe ``compgen -W`` word-list argument from ``values``.

    ``compgen`` re-expands the argument's value at Tab-press (command and
    process substitution, splitting, braces, globbing, quote removal), so each
    value has every character outside the safe set shared with `_zsh_word`
    backslash-escaped; an embedded space or quote then cannot open an
    unmatched region. The joined list is then single-quoted (a quote as
    ``'\\''``) to survive the first, static parse.
    """
    escaped: list[str] = []
    for value in values:
        escaped.append(
            "".join(c if c in _ZSH_WORD_SAFE else "\\" + c for c in str(value))
        )
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
    """Escape ``value`` for zsh's second (dynamic) evaluation.

    ``_arguments`` evaluates an action list like ``(a b c)`` with ``eval`` and
    splits specs on ``:``, so every character outside a conservative safe set
    is backslash-escaped, as in zsh's ``${(q)}``. A leading ``=`` is escaped
    too: ``=cmd`` would expand to that command's path. Apply this first, then
    wrap the joined result in `_sq`.
    """
    word = "".join(c if c in _ZSH_WORD_SAFE else "\\" + c for c in str(value))
    return "\\" + word if word.startswith("=") else word


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

    An embedded single quote is doubled (``''``). A value with a non-ASCII
    character becomes a pure-ASCII expression (quote-doubled ASCII runs joined
    with ``[char]0xNNNN`` per UTF-16 code unit), because PowerShell decodes a
    native command's stdout with the console's OEM code page and would mangle
    raw non-ASCII. This protects the script body; the inserted candidate text
    is quoted separately, see `powershell`.
    """
    text = str(value)
    if text.isascii():
        return "'" + text.replace("'", "''") + "'"
    parts: list[str] = []
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


def _utf16_units(ch: str) -> list[int]:
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
