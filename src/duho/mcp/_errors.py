from __future__ import annotations


class UnknownToolError(ValueError):
    """Raised by :func:`call_tool` for a tool name that does not resolve to a
    callable node in the root class's tree (including a namespace node whose
    own subcommand is mandatory -- see the module docstring). Maps to a
    JSON-RPC ``-32602`` error response in :func:`serve`, never a tool result.
    """

    code = -32602


class InvalidArgumentsError(ValueError):
    """Raised by :func:`call_tool` when ``arguments`` is not a JSON object, or
    fails the target tool's own ``inputSchema`` (an unknown property, or a
    value whose JSON type does not match). Maps to a JSON-RPC ``-32602`` error
    response in :func:`serve`, never a tool result.
    """

    code = -32602
