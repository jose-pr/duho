from __future__ import annotations

import shlex as _shlex


def _bashq(value: object) -> str:
    """POSIX-shell-quote a single value used as a direct bash argument."""
    return _shlex.quote(str(value))


def _bash_wordlist(values: list) -> str:
    """Build a safe ``compgen -W`` word-list argument from ``values``.

    ``compgen -W`` gives its word-list argument a SECOND evaluation at
    Tab-press: after the shell parses/sources the generated script, ``compgen``
    itself re-splits and re-expands that argument's runtime VALUE as if it
    were freshly typed input -- command substitution, BOTH forms of process
    substitution (``<(...)``/``>(...)``, which need no leading ``$`` and so
    survive a narrower escape list untouched), word splitting, brace
    expansion, globbing, and quote removal, all of it. Backslash-escape every
    character outside the same conservative safe set `_zsh_word`/`_fish_word`
    use (so ``< > ( ) * ? [ ~ { } ! & |`` and whitespace are all covered, not
    just backslash/``$``/backtick/quotes), which also means an embedded space
    or quote can no longer open a second, unmatched region at that re-evaluation and swallow
    every later value into one mangled candidate. Escaping the value list is
    not enough on its own: the joined values still ride inside one
    single-quoted argument for THIS (the static) parse, so once each value is
    safe for the second pass, single-quote the whole list (embedded single
    quotes as ``'\\''``) to survive the first.
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
    """Escape ``value`` for zsh's SECOND (dynamic) evaluation.

    zsh's ``_arguments`` builds an action list like ``(a b c)`` with
    ``eval``, and splits a message/action spec on ``:``. Backslash-escape
    every character outside a conservative safe set -- the same approach
    zsh's own ``${(q)}`` quoting uses -- so a value survives that second
    pass literally: whitespace, ``$`` `` ` `` ``()[]{}`` ``;|&<>`` ``'"``
    ``*?~#^!`` and ``:`` all become a literal character instead of shell
    syntax. Apply this FIRST, then wrap the joined result in `_sq` for the
    static parse. A leading ``=`` is escaped too: zsh's ``=cmd`` expansion
    would otherwise replace the word with the path of that command.
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
