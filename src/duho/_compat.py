"""Version compatibility shims for duho.

Centralizes all version-specific logic and fallbacks.
"""

import logging as _logging
import sys as _sys
import types as _types
import typing as _ty

# Union type origins: Union on all versions, UnionType only 3.10+
UNION_ORIGINS: tuple = (
    _ty.Union,
    *([_types.UnionType] if hasattr(_types, "UnionType") else []),
)

#: The one true set of truthy/falsy text tokens: every
#: bool-ish text parser in duho (the layered CLI/env/config converter, the
#: strict CLI text factory, ``Env.bool``, ``logging.traceback_enabled``)
#: matches against these, case-insensitively after ``.strip()``, instead of
#: keeping its own hand-copied set. They had already drifted (logging's
#: falsey set lacked "n"/"f", so ``DUHO_TRACEBACK=n`` turned tracebacks ON
#: while every declared bool field and ``AGENT_HELP`` treated "n" as off).
BOOL_TRUE: frozenset = frozenset({"1", "true", "yes", "on", "y", "t"})
BOOL_FALSE: frozenset = frozenset({"0", "false", "no", "off", "n", "f", ""})


def get_level_names_mapping() -> dict[str, int]:
    """Get mapping of level names to level integers.

    Fallback for Python < 3.11, which lacks getLevelNamesMapping (added in
    3.11, not 3.10).
    """
    if hasattr(_logging, "getLevelNamesMapping"):
        return _logging.getLevelNamesMapping()
    return _logging._nameToLevel.copy()


def iter_entry_points(group: str) -> "list":
    """Return the installed-distribution entry points in ``group`` (F6).

    Bridges the two ``importlib.metadata.entry_points`` shapes:

    * **3.10+** -- ``entry_points(group=...)`` accepts a ``group`` keyword and
      returns a selectable view of the matching entry points, already
      de-duplicated by distribution: when the same distribution name is
      visible more than once on ``sys.path`` (user site + venv, a stray
      ``.egg-info``/``.dist-info`` left in the CWD or on ``PYTHONPATH``), only
      the first copy found contributes its entry points.
    * **3.9** -- ``entry_points()`` takes no arguments, returns a plain
      ``dict`` keyed by group name, and does NOT de-duplicate by
      distribution. Left as-is, a duplicated distribution returned every
      entry point twice, which made ``duho.app``'s collision registry log
      a bogus "registered by more than one source" WARNING on every
      invocation, help included. Mirrors 3.10+'s own dedup here:
      iterate distributions directly, skip one whose normalized name was
      already seen (first copy on ``sys.path`` wins, matching 3.10+), and
      collect only the matching group's entry points from what's left.

    ``importlib.metadata`` is imported lazily *inside* this helper (never at
    module top) so a plain ``import duho`` never pays its import cost -- only an
    app that actually opts into ``entry_points=`` discovery triggers the load
    (startup budget).
    """
    import importlib.metadata as _md

    try:
        return list(_md.entry_points(group=group))
    except TypeError:
        # Python 3.9 fallback (see docstring above).
        import re as _re

        seen_names: "set[str]" = set()
        result: "list" = []
        for dist in _md.distributions():
            name = (dist.metadata or {}).get("Name")
            if name:
                normalized = _re.sub(r"[-_.]+", "-", name).lower()
                if normalized in seen_names:
                    continue
                seen_names.add(normalized)
            for ep in dist.entry_points:
                if ep.group == group:
                    result.append(ep)
        return result


def write_machine(text: str, stream=None) -> None:
    """Write machine-consumed (non-prose) text -- JSON agent-help documents, a
    completion script, an MCP frame -- as literal UTF-8 bytes with LF-only
    newlines.

    On Windows, writing through the normal ``stream.write(str)`` text layer
    (1) translates every ``\\n`` to ``\\r\\n``, and (2) encodes using the
    console/redirect code page (``cp1252`` on this machine), which raises
    ``UnicodeEncodeError`` -- empty output, exit 1 -- for any character
    outside it (arrows, checkmarks, CJK/Greek text), and silently mangles
    Latin-1 text a UTF-8-expecting reader then rejects. Writing raw UTF-8
    bytes to the stream's underlying binary ``.buffer`` (present on a real
    ``sys.stdout``/text file, Windows included) sidesteps both, so a machine
    document survives any host locale. Falls back to the plain text
    ``.write`` for a stream with no ``.buffer`` (``io.StringIO``, a caller's
    own non-binary-backed file-like).
    """
    if stream is None:
        stream = _sys.stdout
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        stream.flush()
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:
        stream.write(text)


def write_human(text: str, stream=None) -> None:
    """Write human-facing prose -- help text, a CLI's status/error messages --
    tolerating any character the stream's own encoding can't represent.

    Unlike :func:`write_machine`, human output should still look right in the
    reader's own terminal/code page (an accented letter renders correctly
    under ``cp1252``), so this keeps the stream's own encoding rather than
    forcing UTF-8 -- only a character genuinely outside that encoding is
    escaped (``errors="backslashreplace"``, e.g. ``\\u2192`` for an arrow)
    instead of raising ``UnicodeEncodeError`` and losing the whole message.
    """
    if stream is None:
        stream = _sys.stdout
    buffer = getattr(stream, "buffer", None)
    encoding = getattr(stream, "encoding", None) or "utf-8"
    if buffer is not None:
        stream.flush()
        buffer.write(text.encode(encoding, errors="backslashreplace"))
        buffer.flush()
    else:
        try:
            stream.write(text)
        except UnicodeEncodeError:
            stream.write(
                text.encode(encoding, errors="backslashreplace").decode(encoding)
            )


__all__ = [
    "UNION_ORIGINS",
    "BOOL_TRUE",
    "BOOL_FALSE",
    "get_level_names_mapping",
    "iter_entry_points",
    "write_machine",
    "write_human",
]
