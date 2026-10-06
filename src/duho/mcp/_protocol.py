from __future__ import annotations

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

#: Fallback ``serverInfo.name`` (:func:`_server_info`) when the served app's own
#: name comes up empty.
_SERVER_NAME = "duho.mcp"


def _error_response(req_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


#: Cap on JSON bracket nesting in one request line (:func:`_line_nesting_exceeds`).
#: `json.loads` recurses per level, so a line of thousands of `[` raises an
#: uncaught `RecursionError`; 64 is far above any real tool call.
_MAX_JSON_NESTING = 64


def _line_nesting_exceeds(line: str, limit: int) -> bool:
    """Whether `line`'s ``{``/``[`` nesting outside JSON strings exceeds `limit`.

    A cheap non-recursive scan that runs before `json.loads`. An unmatched
    close bracket is ignored: `json.loads` rejects that line itself.
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


def _server_info(root_cls: _ty.Union[type, _ServerCore]) -> dict:
    """``serverInfo`` for the ``initialize`` response.

    ``name`` is the root tool-name segment (``core.root_parser.prog``).
    ``version`` is the app's own ``_version_`` when it resolves to a string,
    else ``""``: the MCP ``Implementation`` type requires a string, and duho's
    own version must never stand in for the served app's.
    """
    core = root_cls if isinstance(root_cls, _ServerCore) else _core_for_class(root_cls)
    name = core.root_parser.prog
    version = _resolve_version(core.root_cls)
    return {
        # `prog` is non-empty in every reachable path; the fallback is defensive.
        "name": name if name else _SERVER_NAME,
        "version": version if isinstance(version, str) else "",
    }


def _handle_request(root_cls: type[_Cmd], request: object) -> dict | None:
    """Dispatch one decoded JSON-RPC request; return the response dict, or ``None``.

    ``None`` means no response: a notification (no ``id``), a client's reply,
    or a malformed envelope that carries no ``id``. A batch is fanned out by
    :func:`serve`, so this sees one request object.

    Every value taken from ``request`` is type-checked, so a malformed request
    gets an error response instead of ending :func:`serve`: ``-32600`` for a
    bad request shape, ``-32601`` for an unknown method, ``-32602`` for an
    unknown tool or invalid arguments, ``-32603`` for any other exception.
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
