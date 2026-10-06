from __future__ import annotations

import typing as _ty

from ..args import Cmd as _Cmd


class McpCmd(_Cmd):
    """Serve the CLI currently being dispatched as an MCP server (stdio).

    A ready-made building block: register a
    subclass of this under any name to add a self-serving MCP subcommand to
    a CLI, with zero server code of your own -- ``__call__`` just forwards
    to :func:`serve_running_app`. ``duho.app``'s own opt-in ``_mcp_command_``
    class attribute / ``mcp_command=`` kwarg do exactly this (a dynamically
    named subclass), but any app may register ``McpCmd`` (or a subclass
    adding, say, its own additional flags) under any name it likes, via
    ``_subcommands_``/``commands=``/self-registration -- there is nothing
    ``_mcp_command_`` does that isn't equally reachable by hand.

    A node whose class IS (or subclasses) ``McpCmd`` is never listed as an
    MCP tool, and never callable via ``call_tool`` either (see
    :func:`_is_mcp_command_node`) -- a client asking a live MCP server to
    recursively take over stdio again makes no sense.
    """

    transport: _ty.Literal["stdio"] = "stdio"
    "MCP transport to serve this CLI over"
    ("--transport",)

    def __call__(self) -> int:
        from ._stdio import serve_running_app

        return serve_running_app(self.transport)
