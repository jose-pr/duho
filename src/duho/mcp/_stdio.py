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


def _write_message(stream: object, message: "dict | list") -> None:
    import json

    # `message` is a `list` only for a JSON-RPC *batch* reply (one combined
    # array of response objects, see `serve`); a single response is always
    # a `dict`.
    #
    # `ensure_ascii=True` (never False): the OUTPUT stream's own encoding is
    # not always known to be UTF-8-safe (an injected stream, or a
    # not-yet-reconfigured real stdout), so every non-ASCII character is
    # escaped to a plain-ASCII `\uXXXX` sequence -- still valid JSON, and
    # correct for any text stream whatsoever.
    #
    # `json.dumps` itself can fail on a pathological response -- a `result`
    # holding an unserializable object, or a deeply nested structure echoed
    # back from the request (e.g. its own `id`) that overflows the C
    # recursion limit `RecursionError` guards. Either way this must still
    # produce SOME reply line rather than raise out of `serve`'s loop (which
    # would end the server for every other in-flight/future request), so a
    # failure here falls back to a minimal, always-serializable error
    # response instead of the original message.
    try:
        text = json.dumps(message, ensure_ascii=True)
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


def _real_stdio_streams() -> "tuple":
    """Take ownership of the real stdio fds for the JSON-RPC protocol channel,
    and isolate fd 0/1 from anything a dispatched command does.

    Duplicates the CURRENT fd 0/1 for the protocol itself -- the OUTPUT side
    as a fresh UTF-8/LF-normalised text stream, the INPUT side as a BINARY
    stream (see :func:`serve`, which decodes it one line at a time so a
    single malformed line can be rejected without losing the rest of the
    session) -- then points the process's real fd 1 at fd 2 (stderr) and
    fd 0 at the null device for the rest of the server's life, and rebinds
    ``sys.stdout``/``sys.stdin`` to match. This is what makes the module
    docstring's "one broken command never crashes the whole server loop"
    promise hold even against code the command doesn't control:

    * a subprocess the command spawns WITHOUT capturing its own output
      inherits fd 1 -- now stderr, not the protocol pipe -- instead of
      injecting non-JSON lines into the stream a client is trying to parse;
    * a command honoring the "'-' = stdin" convention, or any low-level
      ``os.read(0, ...)``, gets an immediate EOF on the (now devnull) fd 0
      instead of consuming the client's NEXT request.

    Called directly by :func:`main` -- **before** it resolves ``<app>`` --
    so the takeover is already in effect for the whole rest of the process's
    life by the time anything imports the caller's code (see :func:`main`'s
    docstring for why the ordering matters). Also reachable as
    :func:`serve`'s own fallback when a caller invokes it directly with
    neither ``stdin`` nor ``stdout`` injected; a test driving ``serve`` over
    ``io.StringIO`` is unaffected either way.
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
    root_cls: "_ty.Union[type, _ServerCore]",
    *,
    stdin: "_ty.Optional[_ty.TextIO]" = None,
    stdout: "_ty.Optional[_ty.TextIO]" = None,
) -> int:
    """Run the stdio JSON-RPC loop for ``root_cls`` until stdin closes (EOF).

    ``root_cls`` is a ``Cmd``/``Cli`` class (the static ``_subcommands_``
    tree path) or a :class:`_ServerCore` (an ``app()``-built tree, from
    :func:`_core_for_app`, or the one :func:`serve_running_app` builds from
    the currently-dispatching app's own context) -- forwarded opaquely to
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

    def _safe_handle(request):
        """`_handle_request`, with any unexpected exception mapped to a
        `-32603` response instead of propagating and ending the loop."""
        try:
            return _handle_request(root_cls, request)
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
            request = json.loads(line)
        except (ValueError, RecursionError):
            # `ValueError` is `json.loads`'s own documented failure
            # (`JSONDecodeError` is a `ValueError` subclass); `RecursionError`
            # is not one, and without the nesting check above a deeply
            # nested line would otherwise raise it uncaught here, ending the
            # whole `serve` loop -- kept as a second layer of defense in case
            # some other line shape ever reaches the C decoder's own
            # recursion limit despite that check.
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
