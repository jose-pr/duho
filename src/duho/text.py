"""Zero-dependency string and name utilities.

Brace-range expansion (:func:`expand`), Python-safe name coercion
(:func:`pysafe`), case conversion (:func:`snakecase`, :func:`camelcase`),
and a :mod:`gettext` shim."""

from __future__ import annotations

import itertools as _itertools
import keyword as _keyword
import re as _re
import typing as _ty

__all__ = [
    "gettext",
    "ngettext",
    "snakecase",
    "camelcase",
    "kebabcase",
    "pysafe",
    "expand",
    "PYREPLACE",
    "parse_bool",
    "BOOL_TRUE",
    "BOOL_FALSE",
]

try:
    from gettext import gettext, ngettext
except ImportError:  # pragma: no cover - gettext is always present in CPython

    def gettext(message: str) -> str:
        return message

    def ngettext(singular: str, plural: str, n: int) -> str:
        return singular if n == 1 else plural


#: The truthy/falsy text tokens every bool-ish text parser in duho matches
#: against (CLI, env and config values, ``Env.bool``), case-insensitively after
#: ``.strip()``. ``""`` is falsy.
BOOL_TRUE: _ty.FrozenSet[str] = frozenset({"1", "true", "yes", "on", "y", "t"})
BOOL_FALSE: _ty.FrozenSet[str] = frozenset({"0", "false", "no", "off", "n", "f", ""})


def parse_bool(text: str) -> bool:
    """Parse ``text`` as a strict boolean.

    Case-insensitive after stripping whitespace: a member of :data:`BOOL_TRUE`
    gives ``True``, a member of :data:`BOOL_FALSE` (including the empty string)
    gives ``False``. Anything else, and any non-string, raises
    :class:`ValueError` naming the accepted tokens.
    """
    if isinstance(text, str):
        low = text.strip().lower()
        if low in BOOL_TRUE:
            return True
        if low in BOOL_FALSE:
            return False
    raise ValueError(
        f"{text!r} is not a valid boolean "
        f"(expected one of {sorted(BOOL_TRUE | BOOL_FALSE - {''})})"
    )


def snakecase(name: str) -> str:
    """Coerce ``name`` to ``snake_case``.

    Separators (``-``, whitespace, ``_``) collapse to a single ``_``; a leading
    digit and any other non-word character are underscored; an upper-case letter
    is lower-cased, prefixed with ``_`` UNLESS it already immediately follows a
    separator (so a title-cased or kebab-case word never produces a doubled
    ``__``: ``CamelCaseName`` -> ``camel_case_name``, ``My-App`` -> ``my_app``,
    not ``my__app``). An acronym run is lowered as individual letters
    (``HTTPServer`` -> ``h_t_t_p_server``). An empty string returns ``""``.
    """
    if not name:
        return ""
    std = _re.sub(r"[-\s_]+", "_", name)
    std = _re.sub(r"\W|^(?=\d)", "_", std)
    std = std[0].lower() + std[1:]

    def _lower(match: _re.Match[str]) -> str:
        idx = match.start()
        prefix = "" if idx == 0 or std[idx - 1] == "_" else "_"
        return prefix + match.group(0).lower()

    return _re.sub(r"[A-Z]", _lower, std)


#: Boundary positions :func:`kebabcase` splits on: a lower/digit char
#: immediately followed by an upper char (``fooBar``/``ipv4Tool``), an upper
#: char immediately followed by an upper+lower pair -- the LAST letter of an
#: acronym run, so ``HTTPStatus`` splits as ``HTTP`` | ``Status`` rather than
#: letter-by-letter -- and one or more ``_`` (consumed, not just a boundary).
_KEBAB_BOUNDARY = _re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|_+")


def kebabcase(name: str) -> str:
    """Coerce ``name`` to acronym-aware ``kebab-case``.

    Unlike :func:`snakecase` (which lowers an acronym run letter-by-letter,
    ``HTTPServer`` -> ``h_t_t_p_server``), an acronym run is kept together up
    to its last letter: ``ShowHTTPStatus`` -> ``show-http-status``,
    ``PyTrueNAS`` -> ``py-true-nas``. Splits also occur at a digit->Upper
    boundary (``Ipv4Tool`` -> ``ipv4-tool``) and at one or more ``_``
    (``_Private`` -> ``private``; a leading/trailing/doubled ``_`` never
    yields a leading/trailing/doubled ``-``, since an empty split part is
    dropped). Text with no such boundary -- including one already
    hyphenated, e.g. ``"already-kebab"`` -- passes through unchanged (lower-
    cased, though it already is in that case). Every part is lower-cased
    independently, so mixed case elsewhere in a part (e.g. inside an existing
    hyphenated word) is preserved verbatim other than casing. An empty string
    returns ``""``.

    This is the one rule behind duho's own kebab-case derived names (a
    command's default subcommand name, the default long flag of a field) --
    see ``duho.args._command_name`` / the field-flag default in ``duho.args``.
    """
    if not name:
        return ""
    return "-".join(part.lower() for part in _KEBAB_BOUNDARY.split(name) if part)


#: Symbol -> word replacements applied by :func:`pysafe`.
PYREPLACE = {"+": "plus", "!": "not", "*": "all"}

#: Any character remaining after :func:`pysafe`'s substitutions that is still
#: not valid in a Python identifier. Deliberately narrower than "not
#: ``str.isidentifier``" -- it is only ever applied by :func:`_pysafe_fixup`
#: to a part that already failed that check, so a valid non-ASCII identifier
#: (PEP 3131 permits Unicode letters) is never reached, let alone mangled.
_NON_IDENTIFIER = _re.compile(r"[^0-9A-Za-z_]")


def _pysafe_fixup(part: str) -> str:
    """Make one already ``separator``-split ``part`` a valid identifier.

    A no-op whenever ``part`` is already a valid, non-keyword identifier --
    including one containing non-ASCII letters, which Python's own grammar
    accepts (PEP 3131) and this must never rewrite. Otherwise: any character
    not in ``[0-9A-Za-z_]`` is underscored, a leading digit is prefixed with
    ``_``, an empty part becomes ``"_"``, and a part that is a keyword (either
    still, or newly, after the substitutions above) gets a trailing
    underscore.
    """
    if part and part.isidentifier() and not _keyword.iskeyword(part):
        return part
    part = _NON_IDENTIFIER.sub("_", part)
    if not part:
        part = "_"
    elif part[0].isdigit():
        part = "_" + part
    if _keyword.iskeyword(part):
        part = part + "_"
    return part


def pysafe(text: str, separator: str = ".") -> str:
    """Coerce ``text`` into a Python-safe (dotted) identifier.

    Runs duho's original (0.5.x) transform first -- a per-part keyword suffix,
    then a whole-string ``-``/space-to-``_`` substitution, then the
    :data:`PYREPLACE` symbol spell-out, applied across the whole joined string
    rather than to each part in isolation -- so every input that transform
    already turned into a valid identifier still comes out byte-for-byte the
    same here, quirks included (``pysafe("a+")`` is ``"aplus_plus"``, not the
    more obvious ``"a_plus"``; a symbol at a dotted-part boundary, like
    ``pysafe("a.+b")``, can affect the neighboring part). Only where THAT
    result is not already a valid, non-keyword identifier for one of its
    ``separator``-delimited parts does this go further (:func:`_pysafe_fixup`):
    underscoring any character still not identifier-safe, prefixing a leading
    digit, and suffixing a newly-produced keyword. Never returns an empty
    string, and never returns a string containing a reserved keyword as one of
    its dotted parts.
    """
    text = (
        separator.join(
            [(f"{n}_" if _keyword.iskeyword(n) else n) for n in text.split(separator)]
        )
        .replace("-", "_")
        .replace(" ", "_")
    )
    for symbol, replacement in PYREPLACE.items():
        if text == symbol:
            # A bare match becomes the replacement word outright -- but a
            # substitution that happens to produce a keyword itself
            # (PYREPLACE["!"] == "not") must still be suffixed, so this
            # cannot just return early the way duho 0.5.x did.
            text = replacement + ("_" if _keyword.iskeyword(replacement) else "")
            break
        if text.startswith(symbol):
            text = replacement + "_" + text.removeprefix(symbol)
        if text.endswith(symbol):
            text = text.removeprefix(symbol) + "_" + replacement
        text = text.replace(symbol, replacement)
    text = text or "_"
    return separator.join(_pysafe_fixup(part) for part in text.split(separator))


def camelcase(
    text: str, separators: _ty.Optional[_ty.Union[_ty.Sequence[str], str]] = None
) -> str:
    """Join ``text`` into ``CamelCase``, splitting on ``separators``.

    ``separators`` defaults to ``(".", "_", "-")``; a single string is treated
    as one separator.
    """
    if text:
        separators = separators or (".", "_", "-")
        if isinstance(separators, str):
            separators = (separators,)
        for sep in separators:
            # Skip empty parts: a trailing/doubled/leading separator yields ""
            # segments (e.g. "x_".split("_") == ["x", ""]), and part[0] on "" would
            # raise IndexError. Empty segments contribute nothing to CamelCase.
            text = "".join(
                [part[0].upper() + part[1:] for part in text.split(sep) if part]
            )
    return text


_EXPAND_PATTERN = _re.compile(r"\[([A-Za-z0-9]+)-([A-Za-z0-9]+)(:[^\[\]]*)?\]")

# The module shadows the builtin ``range`` below; alias it first so both the
# local ``range`` and the recursion in ``unicode_range`` can still reach the
# builtin.
_range = range


def unicode_range(start: str, end: str, step: int = 1) -> _ty.Iterator[str]:
    """Yield characters from ``start`` to ``end`` inclusive."""
    for c in _range(ord(start), ord(end) + 1, step):
        yield chr(c)


def range(
    start: str, end: str, step: int = 1, format: _ty.Optional[str] = None
) -> _ty.Iterator[str]:
    """Yield formatted range members between ``start`` and ``end`` inclusive.

    Digit endpoints produce an integer range; single-letter endpoints of the
    SAME case produce a character range. ``format`` is an optional
    ``str.format`` spec (including its leading ``:``, e.g. ``":03d"``) applied
    to each member. Shadows the builtin ``range`` inside this module by design
    (not exported on :data:`__all__`, so ``from duho.text import *`` cannot
    shadow it for a star-importer).

    Raises :class:`ValueError` -- never silently yields an empty or surprising
    range -- for: mismatched-kind endpoints (one digit, one letter), a
    multi-character letter endpoint, mixed-case letter endpoints (which would
    otherwise walk the ASCII punctuation between the two cases), or a reversed
    range (``start`` after ``end``), or a ``format`` containing braces or one the
    member does not accept (``":03d"`` on a letter range).
    """
    format = format or ""
    if "{" in format or "}" in format:
        raise ValueError("range format must not contain braces: %r" % format)
    start_is_digit = start.isdigit()
    end_is_digit = end.isdigit()
    if start_is_digit != end_is_digit:
        raise ValueError(
            "range endpoints must be the same kind (both digits or both "
            "letters): %r-%r" % (start, end)
        )

    if start_is_digit:
        start_i, end_i = int(start), int(end)
        if start_i > end_i:
            raise ValueError("reversed range: %r-%r" % (start, end))
        func = lambda a, b, c: _range(a, b + 1, c)  # noqa: E731
        args = (start_i, end_i, step)
    else:
        if len(start) != 1 or len(end) != 1:
            raise ValueError(
                "letter range endpoints must be single characters: %r-%r" % (start, end)
            )
        if start.isupper() != end.isupper():
            raise ValueError(
                "letter range endpoints must be the same case: %r-%r" % (start, end)
            )
        if ord(start) > ord(end):
            raise ValueError("reversed range: %r-%r" % (start, end))
        func = unicode_range
        args = (start, end, step)

    for i in func(*args):  # type: ignore[arg-type]
        yield f"{{{format}}}".format(i)


def expand(text: str) -> _ty.Iterator[str]:
    """Expand ``[a-b]`` brace ranges in ``text``.

    ``expand("host[01-03]")`` yields ``host1``, ``host2``, ``host3`` (output is
    NOT zero-padded); ``expand("x[A-C]")`` yields ``xA``, ``xB``, ``xC``.
    A ``:spec`` suffix inside the brackets is a ``str.format`` spec applied to
    each member, so ``expand("host[1-3:02d]")`` yields ``host01``, ``host02``,
    ``host03``; a spec with braces, or one the member does not accept, raises
    :class:`ValueError`.
    Multiple ranges expand as their Cartesian product, computed iteratively
    (:func:`itertools.product`) rather than by recursion, so the number of
    ranges in ``text`` is not bounded by Python's recursion limit -- but in the
    same order the original recursive implementation produced: the LEFTMOST
    range varies fastest and the RIGHTMOST varies slowest (``"x[1-2][a-b]"``
    yields ``x1a``, ``x2a``, ``x1b``, ``x2b`` -- the opposite of
    :func:`itertools.product`'s own left-to-right nesting, which is why the
    ranges are iterated in reverse and each combination un-reversed before
    use). Text with no range is yielded unchanged. A malformed range
    (reversed, mismatched-kind, a multi-character/mixed-case letter
    endpoint, or a bad format spec) raises :class:`ValueError` rather than silently yielding nothing
    or a surprising result -- see :func:`range`.
    """
    matches = list(_EXPAND_PATTERN.finditer(text))
    if not matches:
        yield text
        return

    literals: list[str] = []
    choices: list[list[str]] = []
    pos = 0
    for match in matches:
        literals.append(text[pos : match.start()])
        start, end, fmt = match.group(1), match.group(2), match.group(3)
        choices.append(list(range(start, end, format=fmt)))
        pos = match.end()
    literals.append(text[pos:])

    for reversed_combo in _itertools.product(*reversed(choices)):
        combo = tuple(reversed(reversed_combo))
        pieces = [literals[0]]
        for value, literal in zip(combo, literals[1:]):
            pieces.append(value)
            pieces.append(literal)
        yield "".join(pieces)
