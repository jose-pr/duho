"""Version compatibility shims for duho.

Centralizes all version-specific logic and fallbacks.
"""

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

#: The currently-dispatching app's MCP-serving context, set by
#: ``duho.main``/``duho.app`` around their own dispatch step so
#: ``duho.mcp.serve_running_app`` (called from within a dispatched command,
#: e.g. ``McpCmd``) can serve the SAME already-built tree, with no
#: rediscovery. A plain tuple -- ``("class", cls)`` for a static
#: ``_subcommands_`` tree (``duho.main``), or ``("app", parser, root_cls,
#: dispatch)`` for a full ``app()`` build -- kept opaque here on purpose:
#: this module is a leaf (imports nothing internal), so BOTH writers
#: (``args.main``, ``runtime.app``) and the one reader (``duho.mcp``, which
#: neither writer may import at module top) can reach it with no circular
#: import. ``default=None`` means "no app is currently dispatching".
_MCP_CONTEXT: "_contextvars.ContextVar" = _contextvars.ContextVar(
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


def iter_entry_points(group: str) -> "list":
    """Return the installed-distribution entry points in ``group``.

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

    **Tries the normal ``stream.write(text)`` FIRST**, unlike
    :func:`write_machine` (which always goes straight to the raw
    ``.buffer``): the ordinary text layer is what applies the platform's own
    newline translation (``\\n`` -> ``\\r\\n`` on Windows) -- the same
    translation ``print()``/argparse's own ``print_help()`` get for free.
    Human console text is meant to look native, so this must NOT bypass that
    translation in the common case (a fix that regressed this once: routing
    unconditionally through ``.buffer`` avoided the encoding crash but also
    silently dropped every ``--help`` output to LF-only on Windows). The
    ``.buffer`` fallback -- which, writing raw bytes, is necessarily LF-only
    -- is used only for the genuinely-unrepresentable-character case
    ``write_machine`` exists for; that trade-off (a rare escaped line's
    newline no longer gets translated either) is accepted rather than losing
    the message.
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
    streams: "_ty.Optional[_ty.Mapping[str, _ty.Any]]" = None,
) -> "list[str]":
    """Reconfigure text streams to UTF-8 in place, so piped/redirected output
    can never crash with ``UnicodeEncodeError`` for a non-ASCII character the
    host's default locale encoding (``cp1252`` on Windows, piped/captured)
    cannot represent. UTF-8 is the only encoding that cannot raise here.

    ``streams`` defaults to ``{"stdout": sys.stdout, "stderr": sys.stderr}``;
    pass an explicit mapping (e.g. of fake streams) to target something else
    -- this is how tests exercise the skip/switch rules without touching the
    real ``sys.stdout``/``sys.stderr``.

    A stream is left ALONE (skipped) when ANY of:

    1. ``PYTHONIOENCODING`` is set (non-empty) in the environment -- the
       user already made an explicit choice for this process; respect it.
       Checked once for the whole call (it's a process-wide setting, not
       per-stream).
    2. Python's own UTF-8 mode is active (``sys.flags.utf8_mode``) -- every
       stream is already UTF-8.
    3. the stream has no ``.reconfigure()`` method -- it was replaced by
       something else (pytest's capture, a plain ``io.StringIO``, ...) that
       duho must not assume text-stream-with-encoding semantics for.
    4. ``stream.isatty()`` is true -- a real terminal already matches the
       user's own locale (and a modern Windows console is UTF-8-capable
       already); forcing it here would fight the terminal's own setup.
    5. its current encoding, normalized via ``codecs.lookup(...).name``, is
       already ``"utf-8"``.

    Otherwise the stream is switched in place --
    ``stream.reconfigure(encoding="utf-8", errors=...)`` -- using the same
    per-stream errors policy Python's own UTF-8 mode uses:
    ``"surrogateescape"`` for the stream named ``"stdout"``,
    ``"backslashreplace"`` for every other name (``"stderr"`` included, and
    the safer default for a caller's own custom stream name -- it can never
    raise, where ``surrogateescape`` assumes a byte round-trip specifically
    appropriate to stdout).

    Never raises: an ``OSError``/``ValueError`` a ``reconfigure()`` call
    itself raises (e.g. an already-closed stream refusing reconfiguration)
    is swallowed and that one stream is left as-is -- every other stream is
    still attempted. Idempotent: a stream already switched to UTF-8 matches
    rule 5 on a later call and is skipped again.

    Returns the list of stream names actually switched (for tests/logging);
    an empty list means every stream was already fine, opted out via
    ``PYTHONIOENCODING``/UTF-8 mode, or is not a real reconfigurable stream.
    """
    if streams is None:
        streams = {"stdout": _sys.stdout, "stderr": _sys.stderr}

    if _os.environ.get("PYTHONIOENCODING"):
        return []
    if getattr(_sys.flags, "utf8_mode", 0):
        return []

    switched: "list[str]" = []
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
