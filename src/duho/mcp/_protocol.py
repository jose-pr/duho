import logging as _logging
import typing as _ty

from ..args import Cmd as _Cmd
from ..args._naming import _resolve_version as _resolve_version
from ..logging import log_exception as _log_exception

from ._call import call_tool
from ._errors import InvalidArgumentsError, UnknownToolError
from ._tree import _ServerCore, _core_for_class, describe_tools

_LOGGER = _logging.getLogger(__package__)

#: MCP protocol versions this server understands, newest first. ``initialize``
#: echoes the client's own ``protocolVersion`` when it is one of these;
#: otherwise it answers with the first (newest) entry.
_SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

#: Fallback ``serverInfo.name`` for the ``initialize`` result
#: (:func:`_server_info`), used only when the served app's own resolution
#: somehow comes up empty. The NORMAL case reports the app's own identity
#: instead: ``name`` is the same root tool-name segment
#: ``describe_tools``/``call_tool`` use, and ``version`` is the app's own
#: ``_version_`` when it resolves to a string -- so a host can tell one
#: served APP apart from another, not just one duho release from another.
#: There is deliberately NO duho-version fallback for ``version``:
#: reporting duho's own release as the served app's version is actively
#: misleading (e.g. a served app with no ``_version_`` of its own used to
#: report duho's version as if it were its own), so an app with no
#: resolvable version reports the empty string instead -- see
#: :func:`_server_info`.
_SERVER_NAME = "duho.mcp"


def _error_response(req_id: object, code: int, message: str) -> "dict":
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


#: Cap on JSON bracket nesting a single request LINE may contain, checked by
#: :func:`_line_nesting_exceeds` before the line is ever handed to
#: `json.loads`. `json`'s C decoder (and `json.dumps` re-encoding a value
#: parsed that deep) recurses once per nesting level, so an attacker-supplied
#: line of ``"[" * N + "]" * N`` raises an uncaught `RecursionError` well
#: below any depth a legitimate MCP request needs -- N in the low thousands
#: on CPython's default recursion limit, fewer on a build with a smaller
#: C stack. 64 is far beyond any real tool-call payload's own nesting while
#: leaving a wide margin under that limit.
_MAX_JSON_NESTING = 64


def _line_nesting_exceeds(line: str, limit: int) -> bool:
    """Return whether `line`'s ``{``/``[`` nesting depth, OUTSIDE any JSON
    string literal, exceeds `limit` -- a cheap, non-recursive scan run
    BEFORE `json.loads` ever sees the line, so a pathologically deep
    array/object is rejected before any recursive parsing of it begins
    (rather than caught only after `json.loads` itself has already
    recursed to the point of raising `RecursionError`, see `serve`).

    A close bracket for an opening this scan never saw (an otherwise
    malformed line) is ignored here -- `json.loads` still rejects the line
    on its own merits; this scan's only job is bounding nesting DEPTH.
    """
    depth = 0
    in_string = False
    escape = False
    for ch in line:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{" or ch == "[":
            depth += 1
            if depth > limit:
                return True
        elif ch == "}" or ch == "]":
            if depth > 0:
                depth -= 1
    return False


def _server_info(root_cls: "_ty.Union[type, _ServerCore]") -> "dict":
    """``serverInfo`` for the ``initialize`` response.

    ``name`` is the same resolution ``describe_tools``/``call_tool`` use for
    the root tool-name segment (``core.root_parser.prog``, the application's
    name -- see :func:`duho.args._app_name`). ``version`` is the app's own
    ``_version_``
    (:func:`duho.args._resolve_version` -- a plain ``str``, the ``AUTO``
    sentinel resolved via ``importlib.metadata``, or a class-level
    ``__version__`` fallback) when it resolves to a string, else the empty
    string -- duho's own version is NEVER reported as the served
    app's version. The MCP ``Implementation`` type requires ``version`` to be
    a string, so the field is still always present; a served app with no
    resolvable version of its own simply reports it empty rather than
    fabricating one (and rather than silently reporting duho's, which used to
    read as "this app's version is 0.6.2" for an app that never said so).
    """
    core = root_cls if isinstance(root_cls, _ServerCore) else _core_for_class(root_cls)
    name = core.root_parser.prog
    version = _resolve_version(core.root_cls)
    return {
        # `prog` is always a real, non-empty string in every reachable
        # path here; the `_SERVER_NAME` fallback exists only so this stays
        # defensively correct rather than reporting an empty name.
        "name": name if name else _SERVER_NAME,
        "version": version if isinstance(version, str) else "",
    }


def _handle_request(root_cls: "type[_Cmd]", request: object) -> "dict | None":
    """Dispatch one decoded JSON-RPC request; return the response dict, or ``None``.

    ``None`` means "no response" -- either the request was a **notification**
    (no ``id`` key at all; JSON-RPC forbids replying to one), the
    ``notifications/initialized`` notification specifically, or a malformed
    envelope that also happened to carry no ``id``.

    Handles exactly ONE request object -- a batch (a JSON array of request
    objects) is recognized and fanned out by :func:`serve` itself, one call
    to this function per element, before this function ever sees it.

    Every value pulled out of ``request`` is type-checked before use, so a
    well-formed JSON document that is not a well-formed JSON-RPC REQUEST
    (a bare scalar, a non-object ``params``, a non-string ``name``, a
    non-object ``arguments``) gets a proper JSON-RPC error response instead
    of an uncaught ``AttributeError``/``TypeError`` that would otherwise
    propagate out of :func:`serve` and end the process
    : ``-32600`` for a malformed request/params shape, ``-32601`` for
    an unrecognised method, ``-32602`` for a call naming an unknown tool or
    supplying invalid arguments (:class:`UnknownToolError`/
    :class:`InvalidArgumentsError`), ``-32603`` as a last resort for any
    other exception raised while actually serving ``tools/list``/
    ``tools/call`` (which must never happen, but must never take the whole
    server down either if it somehow does).
    """
    if not isinstance(request, dict):
        return _error_response(None, -32600, "invalid request: expected a JSON object")

    if "method" not in request and ("result" in request or "error" in request):
        return None  # a client's reply to a request, never a request itself

    method = request.get("method")
    has_id = "id" in request
    req_id = request.get("id")

    # JSON-RPC 2.0: "jsonrpc" is exactly "2.0" and an id is a string or a
    # number; MCP additionally forbids a null request id.
    if request.get("jsonrpc") != "2.0" or (
        has_id
        and (isinstance(req_id, bool) or not isinstance(req_id, (str, int, float)))
    ):
        return (
            _error_response(
                req_id if isinstance(req_id, (str, int)) else None,
                -32600,
                "invalid request: 'jsonrpc' must be \"2.0\" and 'id' a string "
                "or a number",
            )
            if has_id
            else None
        )

    if not isinstance(method, str):
        return (
            _error_response(
                req_id, -32600, "invalid request: 'method' must be a string"
            )
            if has_id
            else None
        )

    params = request.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return (
            _error_response(req_id, -32602, "invalid params: expected an object")
            if has_id
            else None
        )

    if method == "initialize":
        requested = params.get("protocolVersion")
        negotiated = (
            requested if requested in _SUPPORTED_VERSIONS else _SUPPORTED_VERSIONS[0]
        )
        result = {
            "protocolVersion": negotiated,
            "capabilities": {"tools": {}},
            "serverInfo": _server_info(root_cls),
        }
    elif method in ("notifications/initialized", "initialized"):
        return None
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        try:
            result = {"tools": describe_tools(root_cls)}
        except Exception as exc:  # noqa: BLE001 - see docstring
            _log_exception(
                _LOGGER, "tools/list raised: %s: %s", type(exc).__name__, exc
            )
            return (
                _error_response(
                    req_id, -32603, "internal error: %s: %s" % (type(exc).__name__, exc)
                )
                if has_id
                else None
            )
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_arguments = params.get("arguments")
        try:
            result = call_tool(root_cls, tool_name, tool_arguments)
        except (UnknownToolError, InvalidArgumentsError) as exc:
            return _error_response(req_id, exc.code, str(exc)) if has_id else None
        except Exception as exc:  # noqa: BLE001 - see docstring
            _log_exception(
                _LOGGER,
                "tools/call %r raised: %s: %s",
                tool_name,
                type(exc).__name__,
                exc,
            )
            return (
                _error_response(
                    req_id, -32603, "internal error: %s: %s" % (type(exc).__name__, exc)
                )
                if has_id
                else None
            )
    elif method in ("shutdown", "exit"):
        result = None
    elif not has_id:
        return None  # an unrecognised notification: nothing to reply to
    else:
        return _error_response(req_id, -32601, "method not found: %r" % (method,))

    if not has_id:
        return None  # a notification for a method we do handle: still no reply
    return {"jsonrpc": "2.0", "id": req_id, "result": result}
