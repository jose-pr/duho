"""Version compatibility shims for duho.

Centralizes all version-specific logic and fallbacks.
"""

from __future__ import annotations

import codecs as _codecs
import contextvars as _contextvars
import logging as _logging
import os as _os
import sys as _sys
import types as _types
import typing as _ty

from .text import BOOL_FALSE, BOOL_TRUE

# Union type origins: Union on all versions, UnionType only 3.10+
UNION_ORIGINS: tuple = (
    _ty.Union,
    *([_types.UnionType] if hasattr(_types, "UnionType") else []),
)

#: The dispatching app's MCP-serving context, set by ``args.main`` and
#: ``runtime.app`` around dispatch so ``duho.mcp`` and the completion
#: subcommand read the same built tree. Opaque tuple: ``("class", cls)`` or
#: ``("app", parser, root_cls, dispatch)``; None when nothing is dispatching.
#: This module imports nothing internal, so writers and reader avoid a cycle.
_MCP_CONTEXT: _contextvars.ContextVar = _contextvars.ContextVar(
    "duho_mcp_context", default=None
)


# The one truthy/falsy token table lives in ``duho.text``; re-bound here for the
# internal readers.
def get_level_names_mapping() -> dict[str, int]:
    """Get mapping of level names to level integers.

    Fallback for Python < 3.11, which lacks getLevelNamesMapping (added in
    3.11, not 3.10).
    """
    if hasattr(_logging, "getLevelNamesMapping"):
        return _logging.getLevelNamesMapping()
    return _logging._nameToLevel.copy()


def iter_entry_points(group: str) -> list:
    """Return the installed-distribution entry points in ``group``.

    On 3.9, ``entry_points()`` returns a dict and does not de-duplicate a
    distribution visible twice on ``sys.path``; the fallback skips repeats
    (first copy wins, as on 3.10+) so a duplicate does not log a false
    collision warning. ``importlib.metadata`` is imported inside the helper to
    keep ``import duho`` cheap.
    """
    import importlib.metadata as _md

    try:
        return list(_md.entry_points(group=group))
    except TypeError:
        # Python 3.9: no ``group`` keyword.
        import re as _re

        seen_names: set[str] = set()
        result: list = []
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
    """Write machine-consumed text (JSON, a completion script, an MCP frame) as
    UTF-8 bytes with LF-only newlines.

    Goes through ``stream.buffer`` so Windows neither translates ``\\n`` nor
    encodes with the console code page; a stream with no ``.buffer`` (such as
    ``io.StringIO``) gets a plain ``.write``.
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
    """Write human-facing prose, escaping a character the stream's encoding
    cannot represent (``backslashreplace``) instead of raising.

    Tries ``stream.write`` first so the platform's newline translation applies
    as it does for ``print``. Only the unrepresentable-character fallback
    writes raw bytes to ``.buffer``, so that line stays LF-only.
    """
    if stream is None:
        stream = _sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        buffer = getattr(stream, "buffer", None)
        encoding = getattr(stream, "encoding", None) or "utf-8"
        if buffer is not None:
            stream.flush()
            buffer.write(text.encode(encoding, errors="backslashreplace"))
            buffer.flush()
        else:
            stream.write(
                text.encode(encoding, errors="backslashreplace").decode(encoding)
            )


def utf8_stdio(
    streams: _ty.Optional[_ty.Mapping[str, _ty.Any]] = None,
) -> list[str]:
    """Reconfigure text streams to UTF-8 in place; return the names switched.

    ``streams`` defaults to stdout and stderr; tests pass fakes. A stream is
    left alone when ``PYTHONIOENCODING`` is set, UTF-8 mode is on, it has no
    ``reconfigure()``, it is a tty, or it is already UTF-8. Errors policy:
    ``surrogateescape`` for ``"stdout"``, ``backslashreplace`` otherwise. Never
    raises: a failing ``reconfigure()`` skips that stream.
    """
    if streams is None:
        streams = {"stdout": _sys.stdout, "stderr": _sys.stderr}

    if _os.environ.get("PYTHONIOENCODING"):
        return []
    if getattr(_sys.flags, "utf8_mode", 0):
        return []

    switched: list[str] = []
    for stream_name, stream in streams.items():
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            is_tty = stream.isatty()
        except Exception:
            is_tty = False
        if is_tty:
            continue
        encoding = getattr(stream, "encoding", None)
        if encoding:
            try:
                if _codecs.lookup(encoding).name == "utf-8":
                    continue
            except LookupError:
                pass
        errors = "surrogateescape" if stream_name == "stdout" else "backslashreplace"
        try:
            reconfigure(encoding="utf-8", errors=errors)
        except (OSError, ValueError):
            continue
        switched.append(stream_name)
    return switched


__all__ = [
    "UNION_ORIGINS",
    "BOOL_TRUE",
    "BOOL_FALSE",
    "get_level_names_mapping",
    "iter_entry_points",
    "utf8_stdio",
    "write_machine",
    "write_human",
]
