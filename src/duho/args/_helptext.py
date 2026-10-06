from __future__ import annotations

import re as _re

from .. import _compat as _compat

#: A well-formed ``%(key)conversion`` mapping placeholder, or an already-
#: doubled ``%%`` literal -- the only two forms argparse's own ``%``-format
#: expansion (`text % dict(...)`) accepts once it is asked to format against
#: a MAPPING (which every call site here does): a bare `%s`/`%d` with no
#: `(key)` is invalid against a dict and raises just as badly as a stray `%`.
_PERCENT_PLACEHOLDER = _re.compile(r"%\([^)]*\)[a-zA-Z]|%%")


def _escape_stray_percent(text: str) -> str:
    """Escape a literal ``%`` to ``%%`` EXCEPT inside an already-valid
    ``%(key)s``-style placeholder (or an already-doubled ``%%``).

    Existing code (see :class:`duho.formatters.DefaultsFormatter`, which
    explicitly checks ``"%(default)" in help_text`` and leaves it alone) lets
    an author write a REAL ``%(default)s``/``%(prog)s`` placeholder directly
    in help/description text and have argparse expand it normally -- a
    blanket ``text.replace("%", "%%")`` would silently turn that intentional
    placeholder into inert literal text too, alongside the stray, crash-
    prone ``%`` (e.g. "50% of CPUs") this escaping exists to neutralize.
    Preserving already-valid placeholders and escaping everything else keeps
    both working.
    """
    if "%" not in text:
        return text
    pieces: list[str] = []
    pos = 0
    for match in _PERCENT_PLACEHOLDER.finditer(text):
        pieces.append(text[pos : match.start()].replace("%", "%%"))
        pieces.append(match.group(0))
        pos = match.end()
    pieces.append(text[pos:].replace("%", "%%"))
    return "".join(pieces)


def _escape_help(text: str) -> str:
    """Escape a literal ``%`` to ``%%`` for text handed to argparse as a
    ``help=`` string.

    argparse's ``HelpFormatter._expand_help`` (or, on 3.14+, ``add_argument``'s
    own ``_check_help``) always ``%``-formats an action/subcommand ``help=``
    string, so a literal ``%`` in docstring-derived help text (a field
    docstring, a class docstring's one-line subcommand summary, a module
    command's docstring) would otherwise crash parser BUILD on Python 3.14 or
    ``--help`` on 3.9. An explicit ``NS(help=...)``/``Meta(help=...)`` is
    applied AFTER this (via the builder-options setattr loop), so an author
    who already wrote a real ``%(default)s`` placeholder there is untouched
    either way; :func:`_escape_stray_percent` also leaves one written
    directly in a docstring alone.
    """
    return _escape_stray_percent(text)


def _escape_description(text: str) -> str:
    """Escape a literal ``%`` to ``%%`` for a ``description=`` ONLY when it
    contains a real ``%(prog)`` placeholder.

    Unlike ``help=``, argparse's ``HelpFormatter._format_text`` only
    ``%``-formats a ``description``/epilog when it literally contains the
    substring ``%(prog)`` -- an ordinary description is shown byte-for-byte.
    Escaping every description unconditionally (an earlier fix did) therefore
    made a literal ``%`` show up DOUBLED (``%%``) in ``--help`` for the common
    case; escaping only when the placeholder is actually present keeps both
    correct (and, via :func:`_escape_stray_percent`, keeps the ``%(prog)``
    placeholder ITSELF from also being escaped into inert text).
    """
    return _escape_stray_percent(text) if "%(prog)" in text else text


def _write_machine_text(text: str, file=None) -> None:
    """Write machine-consumed (non-prose) text -- a completion script, an
    agent-help JSON document -- as literal UTF-8 bytes, bypassing the text
    layer's newline translation and console code page.

    Thin alias kept under its original name (used at several call sites in
    this module); the actual implementation is the shared
    ``duho._compat.write_machine`` writer also used by ``duho.agenthelp`` and
    ``duho.mcp``, so every machine-readable output path agrees.
    """
    _compat.write_machine(text, file)
