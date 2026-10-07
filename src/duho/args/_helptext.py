from __future__ import annotations

import re as _re

#: A ``%(key)conversion`` placeholder or a doubled ``%%``: the only forms
#: argparse's %-format accepts against a mapping (a bare `%s` or stray `%` raises).
_PERCENT_PLACEHOLDER = _re.compile(r"%\([^)]*\)[a-zA-Z]|%%")


def _escape_stray_percent(text: str) -> str:
    """Escape a literal ``%`` to ``%%`` except inside a valid ``%(key)s`` placeholder.

    A real ``%(default)s``/``%(prog)s`` written in help text must still expand
    (see :class:`duho.formatters.DefaultsFormatter`), so a blanket
    ``replace("%", "%%")`` would turn it into inert text.

    argparse %-formats every ``help=`` (``_expand_help``, or ``_check_help`` in
    ``add_argument`` on 3.14+), so a stray ``%`` in docstring-derived help would
    crash parser build on 3.14 or ``--help`` on 3.9. An explicit ``NS(help=...)``
    is applied after this and is never escaped.
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


def _escape_description(text: str) -> str:
    """Escape a literal ``%`` to ``%%`` for a ``description=`` only if it has ``%(prog)``.

    argparse %-formats a description only when it contains ``%(prog)``;
    escaping every one would show a doubled ``%`` in ``--help``.
    """
    return _escape_stray_percent(text) if "%(prog)" in text else text

