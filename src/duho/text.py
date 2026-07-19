"""Zero-dependency string and name utilities.

Brace-range expansion (:func:`expand`), Python-safe name coercion
(:func:`pysafe`), case conversion (:func:`snakecase`, :func:`camelcase`),
and a :mod:`gettext` shim.

All union annotations are quoted so the module imports cleanly on Python 3.9,
where an unquoted PEP-604 ``X | Y`` in a signature evaluates at def time and
raises ``TypeError``.
"""

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
    "range",
    "unicode_range",
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
    digit and any other non-word character are underscored; an interior
    upper-case letter is lower-cased and prefixed with ``_`` (``CamelCaseName`` ->
    ``camel_case_name``). An acronym run is lowered as individual letters
    (``HTTPServer`` -> ``h_t_t_p_server``). An empty string returns ``""``.
    """
    if not name:
        return ""
    std = _re.sub(r"[-\s_]+", "_", name)
    std = _re.sub(r"\W|^(?=\d)", "_", std)
    std = std[0].lower() + std[1:]
    return _re.sub(r"(?<!^)[A-Z]", lambda m: "_" + m.group(0).lower(), std)


#: Symbol -> word replacements applied by :func:`pysafe`.
PYREPLACE = {"+": "plus", "!": "not", "*": "all"}


def pysafe(text: str, separator: str = ".") -> str:
    """Coerce ``text`` into a Python-safe (dotted) identifier.

    Keyword parts (``keyword.iskeyword``) get a trailing underscore, hyphens and
    spaces become underscores, and the symbols in :data:`PYREPLACE` are spelled
    out. Never returns an empty string.
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
            return replacement
        if text.startswith(symbol):
            text = replacement + "_" + text.removeprefix(symbol)
        if text.endswith(symbol):
            text = text.removeprefix(symbol) + "_" + replacement
        text = text.replace(symbol, replacement)

    return text or "_"


def camelcase(
    text: str, separators: "_ty.Sequence[str] | str | None" = None
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


_EXPAND_PATTERN = _re.compile(
    r".*(\[([A-Z0-9]+)-([A-Z0-9]+)(:[^\[\]]*)?\]).*", _re.IGNORECASE
)

# The module shadows the builtin ``range`` below; alias it first so both the
# local ``range`` and the recursion in ``expand`` can still reach the builtin.
_range = range


def unicode_range(start: str, end: str, step: int = 1) -> "_ty.Iterator[str]":
    """Yield characters from ``start`` to ``end`` inclusive."""
    for c in _range(ord(start), ord(end) + 1, step):
        yield chr(c)


def range(
    start: str, end: str, step: int = 1, format: "str | None" = None
) -> "_ty.Iterator[str]":
    """Yield formatted range members between ``start`` and ``end`` inclusive.

    Digit endpoints produce an integer range; letter endpoints produce a
    character range. ``format`` is an optional ``str.format`` spec applied to
    each member. Shadows the builtin ``range`` inside this module by design.
    """
    format = format or ""
    if start.isdigit():
        start = int(start)  # type: ignore[assignment]
        end = int(end)  # type: ignore[assignment]
        func = lambda a, b, c: _range(a, b + 1, c)
    else:
        func = unicode_range

    for i in func(start, end, step):  # type: ignore[arg-type]
        yield f"{{{format}}}".format(i)


def expand(text: str) -> "_ty.Iterator[str]":
    """Expand ``[a-b]`` brace ranges in ``text``.

    ``expand("host[01-03]")`` yields ``host1``, ``host2``, ``host3`` (output is
    NOT zero-padded); ``expand("x[A-C]")`` yields ``xA``, ``xB``, ``xC``.
    Multiple ranges are expanded recursively (Cartesian product). Text with no
    range is yielded unchanged.
    """
    template = _EXPAND_PATTERN.match(text)
    if template:
        _, start, end, format = template.groups()
        s, e = template.span(1)
        for i in range(start, end, format=format):
            yield from expand(f"{text[:s]}{i}{text[e:]}")
    else:
        yield text
