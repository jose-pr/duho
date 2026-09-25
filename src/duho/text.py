"""Zero-dependency string and name utilities.

Brace-range expansion (:func:`expand`), Python-safe name coercion
(:func:`pysafe`), case conversion (:func:`snakecase`, :func:`camelcase`),
and a :mod:`gettext` shim.

All union annotations are quoted so the module imports cleanly on Python 3.9,
where an unquoted PEP-604 ``X | Y`` in a signature evaluates at def time and
raises ``TypeError``.
"""

import itertools as _itertools
import keyword as _keyword
import re as _re
import typing as _ty

__all__ = [
    "gettext",
    "ngettext",
    "snakecase",
    "camelcase",
    "pysafe",
    "expand",
    "PYREPLACE",
]

try:
    from gettext import gettext, ngettext
except ImportError:  # pragma: no cover - gettext is always present in CPython

    def gettext(message: str) -> str:
        return message

    def ngettext(singular: str, plural: str, n: int) -> str:
        return singular if n == 1 else plural


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

    def _lower(match: "_re.Match[str]") -> str:
        idx = match.start()
        prefix = "" if idx == 0 or std[idx - 1] == "_" else "_"
        return prefix + match.group(0).lower()

    return _re.sub(r"[A-Z]", _lower, std)


#: Symbol -> word replacements applied by :func:`pysafe`.
PYREPLACE = {"+": "plus", "!": "not", "*": "all"}

#: Any character remaining after :data:`PYREPLACE` substitution that is still
#: not valid in a Python identifier.
_NON_IDENTIFIER = _re.compile(r"[^0-9A-Za-z_]")


def _pysafe_part(part: str) -> str:
    """Coerce one (non-dotted) ``part`` into a valid Python identifier.

    Symbol substitution (:data:`PYREPLACE`) runs first, applied to this part
    alone -- a whole-part match becomes the replacement word outright; a
    leading/trailing symbol becomes ``<replacement>_<rest>``/``<rest>_<replacement>``;
    any other occurrence is spelled out in place. Any character still not valid
    in an identifier is then underscored, a leading digit is prefixed with
    ``_``, and an empty part becomes ``"_"``. The keyword check
    (``keyword.iskeyword``) runs LAST, so a substitution that happens to produce
    a keyword (``PYREPLACE["!"] == "not"``) is still suffixed.
    """
    for symbol, replacement in PYREPLACE.items():
        if part == symbol:
            part = replacement
            break
        if part.startswith(symbol):
            part = replacement + "_" + part[len(symbol) :]
        if part.endswith(symbol):
            part = part[: -len(symbol)] + "_" + replacement
        part = part.replace(symbol, replacement)

    part = part.replace("-", "_").replace(" ", "_")
    part = _NON_IDENTIFIER.sub("_", part)

    if part[:1].isdigit():
        part = "_" + part
    if not part:
        part = "_"
    if _keyword.iskeyword(part):
        part = part + "_"

    return part


def pysafe(text: str, separator: str = ".") -> str:
    """Coerce ``text`` into a Python-safe (dotted) identifier.

    Each ``separator``-delimited part is made into a valid Python identifier
    independently (see :func:`_pysafe_part`): the symbols in :data:`PYREPLACE`
    are spelled out, any other non-identifier character (including hyphens and
    spaces) becomes ``_``, a leading digit is prefixed with ``_``, and a keyword
    part gets a trailing underscore. Never returns an empty string, and never
    returns a string containing a reserved keyword as one of its dotted parts.
    """
    return separator.join(_pysafe_part(part) for part in text.split(separator))


def camelcase(text: str, separators: "_ty.Sequence[str] | str | None" = None) -> str:
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


def unicode_range(start: str, end: str, step: int = 1) -> "_ty.Iterator[str]":
    """Yield characters from ``start`` to ``end`` inclusive."""
    for c in _range(ord(start), ord(end) + 1, step):
        yield chr(c)


def range(
    start: str, end: str, step: int = 1, format: "str | None" = None
) -> "_ty.Iterator[str]":
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
    range (``start`` after ``end``).
    """
    format = format or ""
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


def expand(text: str) -> "_ty.Iterator[str]":
    """Expand ``[a-b]`` brace ranges in ``text``.

    ``expand("host[01-03]")`` yields ``host1``, ``host2``, ``host3`` (output is
    NOT zero-padded); ``expand("x[A-C]")`` yields ``xA``, ``xB``, ``xC``.
    Multiple ranges expand as their Cartesian product, computed iteratively
    (:func:`itertools.product`) rather than by recursion, so the number of
    ranges in ``text`` is not bounded by Python's recursion limit. Text with no
    range is yielded unchanged. A malformed range (reversed, mismatched-kind, or
    a multi-character/mixed-case letter endpoint) raises :class:`ValueError`
    rather than silently yielding nothing or a surprising result -- see
    :func:`range`.
    """
    matches = list(_EXPAND_PATTERN.finditer(text))
    if not matches:
        yield text
        return

    literals: "list[str]" = []
    choices: "list[list[str]]" = []
    pos = 0
    for match in matches:
        literals.append(text[pos : match.start()])
        start, end, fmt = match.group(1), match.group(2), match.group(3)
        choices.append(list(range(start, end, format=fmt)))
        pos = match.end()
    literals.append(text[pos:])

    for combo in _itertools.product(*choices):
        pieces = [literals[0]]
        for value, literal in zip(combo, literals[1:]):
            pieces.append(value)
            pieces.append(literal)
        yield "".join(pieces)
