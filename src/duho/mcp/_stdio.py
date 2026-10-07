from __future__ import annotations

import logging as _logging
import os as _os
import sys as _sys
import typing as _ty

from .. import _compat as _compat
from ..logging import log_exception as _log_exception

from ._protocol import (
    _MAX_JSON_NESTING,
    _error_response,
    _handle_request,
    _line_nesting_exceeds,
)
from ._tree import _ServerCore, _core_for_class, _walk_tree

_LOGGER = _logging.getLogger(__package__)


def serve_running_app(transport: str = "stdio") -> int:
    """Serve the CLI currently being dispatched as an MCP server, over
    ``transport`` (currently only ``"stdio"``).

    Reads the app context :func:`duho.args.main`/:func:`duho.runtime.app`
    record in a ``ContextVar`` (``duho._compat._MCP_CONTEXT``) around their
    own dispatch step -- the exact parser/root class/dispatch callable THAT
    invocation already built -- so serving here needs no rediscovery: this
    is the SAME tree a client would see calling any other tool on the same
    running process, just entered from inside a dispatched command (e.g.
    :class:`McpCmd`) instead of the ``<PREFIX>MCP``/``<NAME>_MCP`` env
    trigger.

    Output printed while the app was built (module-command imports,
    ``register()`` hooks) was written before this was called, so on this path
    it reaches stdout ahead of the first reply; the environment trigger takes
    stdio over before building and has no such output.

    Raises ``RuntimeError`` when called outside such a dispatch (a bare
    script that never went through ``duho.main``/``duho.app`` at all has no
    running app context to serve). Raises ``ValueError`` for an unsupported
    ``transport`` -- checked BEFORE consulting the context, so it is
    reported the same way regardless of whether one exists.
    """
    if transport != "stdio":
        raise ValueError(
            "unsupported MCP transport %r (supported: stdio)" % (transport,)
        )
    ctx = _compat._MCP_CONTEXT.get()
    if ctx is None:
        raise RuntimeError(
            "serve_running_app() was called outside a duho.main()/duho.app() "
            "dispatch -- there is no currently-running app context to serve"
        )
    kind = ctx[0]
    if kind == "class":
        core = _core_for_class(ctx[1])
    else:
        _, parser, root_cls, dispatch = ctx
        nodes = _walk_tree(parser, root_cls, parser.prog)
        core = _ServerCore(parser, nodes, dispatch, root_cls)
    return serve(core)


def _reject_constant(name: str) -> _ty.NoReturn:
    """``json.loads`` hook: ``NaN``/``Infinity`` are not JSON, so a line
    carrying one is a parse error rather than a value echoed back unparsable."""
    raise ValueError("%s is not valid JSON" % (name,))


def _write_message(stream: object, message: dict | list) -> None:
    import json

    # `ensure_ascii=True`: the stream may not be UTF-8-safe. A `json.dumps`
    # failure (unserializable or too deep) sends a minimal error reply instead
    # of ending the `serve` loop.
    try:
        text = json.dumps(message, ensure_ascii=True, allow_nan=False)
    except Exception:
        text = json.dumps(
            _error_response(
                None, -32603, "internal error: failed to serialise response"
            ),
            ensure_ascii=True,
        )
    stream.write(text + "\n")
    flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


def _real_stdio_streams() -> tuple:
    """Take the real stdio fds for the protocol and isolate fd 0/1 from commands.

    Returns ``(stream_in, stream_out)``: duplicates of fd 0 (binary, so
    :func:`serve` can reject one bad line) and fd 1 (UTF-8, LF). Then fd 1 is
    pointed at stderr and fd 0 at the null device, so a spawned subprocess
    cannot write non-JSON into the protocol pipe and a command reading stdin
    gets EOF instead of the client's next request. :func:`main` calls this
    before importing ``<app>``; :func:`serve` falls back to it when no stream
    is injected.
    """
    _sys.stdout.flush()
    proto_in_fd = _os.dup(0)
    proto_out_fd = _os.dup(1)
    devnull_fd = _os.open(_os.devnull, _os.O_RDONLY)
    _os.dup2(devnull_fd, 0)
    _os.close(devnull_fd)
    _os.dup2(2, 1)

    stream_in = _os.fdopen(proto_in_fd, "rb")
    stream_out = _os.fdopen(proto_out_fd, "w", encoding="utf-8", newline="\n")
    _sys.stdin = _os.fdopen(_os.dup(0), "r", encoding="utf-8")
    _sys.stdout = _sys.stderr
    return stream_in, stream_out


def serve(
    root_cls: _ty.Union[type, _ServerCore],
    *,
    stdin: _ty.Optional[_ty.TextIO] = None,
    stdout: _ty.Optional[_ty.TextIO] = None,
) -> int:
    """Run the stdio JSON-RPC loop for ``root_cls`` until stdin closes (EOF).

    ``root_cls`` is a ``Cmd``/``Cli`` class (the static ``_subcommands_``
    tree). The :class:`_ServerCore` form is the server core duho builds for
    an ``app()`` tree (the environment trigger, ``_mcp_command_`` and
    :func:`serve_running_app`); a caller does not construct one. Either is
    forwarded opaquely to
    :func:`_handle_request`, which in turn forwards it to
    :func:`describe_tools`/:func:`call_tool` (both already accept either
    shape -- see :func:`describe_tools`).

    Reads newline-delimited JSON-RPC 2.0 request lines from ``stdin`` (real
    stdio, isolated per :func:`_real_stdio_streams`, when neither ``stdin``
    nor ``stdout`` is given -- BINARY there, so one line's invalid UTF-8
    bytes cannot kill the whole server, see below), dispatches each via
    :func:`_handle_request`, and writes any response line to ``stdout``,
    flushed every time. A line that is not valid UTF-8 (only possible on the
    real-stdio path; an injected text ``stdin`` is decoded already) gets a
    ``-32700`` parse-error response and the loop continues -- a client
    reconnecting or retrying is not required. A line that decodes but fails
    to parse as JSON gets the same ``-32700`` (``id: null`` in both cases --
    the malformed line's own id, if any, is unrecoverable), as does a line
    whose ``{``/``[`` nesting exceeds :data:`_MAX_JSON_NESTING` -- rejected
    BEFORE ``json.loads`` ever parses it, since parsing (or later
    re-encoding) a pathologically deep structure would otherwise raise an
    uncaught ``RecursionError`` and end the loop. A JSON ARRAY
    (a JSON-RPC 2.0 *batch*) is dispatched element by element; every non-
    notification element's response is collected into ONE reply array
    (never sent at all if the batch was all notifications, per spec), and an
    EMPTY batch array gets its own ``-32600``. An unexpected exception from
    :func:`_handle_request` itself (which should never happen, given its own
    internal error handling, but must never end the server if it somehow
    does) becomes a ``-32603`` response instead of propagating. Blank lines
    are skipped. Returns ``0`` when ``stdin`` reaches EOF (there is no
    separate MCP "shutdown" method to wait for). ``stdin``/``stdout`` are
    injectable so tests can drive the loop over in-memory TEXT streams
    instead of real pipes.
    """
    import json

    if stdin is None and stdout is None:
        stream_in, stream_out = _real_stdio_streams()
    else:
        stream_in = stdin if stdin is not None else _sys.stdin
        stream_out = stdout if stdout is not None else _sys.stdout

    session: dict = {}

    def _safe_handle(request):
        """`_handle_request`, with any unexpected exception mapped to a
        `-32603` response instead of propagating and ending the loop."""
        try:
            return _handle_request(root_cls, request, session)
        except Exception as exc:  # noqa: BLE001 - the loop itself must never die
            _log_exception(
                _LOGGER,
                "serve() request handling raised: %s: %s",
                type(exc).__name__,
                exc,
            )
            fallback_id = request.get("id") if isinstance(request, dict) else None
            return _error_response(
                fallback_id,
                -32603,
                "internal error: %s: %s" % (type(exc).__name__, exc),
            )

    for raw_line in stream_in:
        if isinstance(raw_line, bytes):
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                _write_message(
                    stream_out,
                    _error_response(
                        None, -32700, "parse error: invalid utf-8 (%s)" % exc
                    ),
                )
                continue
        else:
            line = raw_line
        line = line.strip()
        if not line:
            continue
        if _line_nesting_exceeds(line, _MAX_JSON_NESTING):
            _write_message(
                stream_out,
                _error_response(None, -32700, "parse error: JSON nesting too deep"),
            )
            continue
        try:
            request = json.loads(line, parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            # `RecursionError` is not a `ValueError`; a second layer behind the
            # nesting check, for a line shape that still overflows the decoder.
            _write_message(stream_out, _error_response(None, -32700, "parse error"))
            continue

        if isinstance(request, list):
            if not request:
                _write_message(
                    stream_out,
                    _error_response(None, -32600, "invalid request: empty batch"),
                )
                continue
            responses = [
                response
                for response in (_safe_handle(item) for item in request)
                if response is not None
            ]
            if responses:
                _write_message(stream_out, responses)
            continue

        response = _safe_handle(request)
        if response is not None:
            _write_message(stream_out, response)
    return 0
