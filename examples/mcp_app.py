#!/usr/bin/env python3
"""A runnable app demonstrating ``duho.mcp`` (the MCP tool surface, opt-in).

Reuses ``fileinstall.py``'s own ``Install``/``FileInstall`` app -- the point of
this example isn't a new CLI, it's showing that an EXISTING duho CLI, unmodified,
is already a complete MCP tool surface: no redeclaration, no second schema to keep
in sync.

Run it as a real MCP stdio server::

    python -m duho.mcp mcp_app:FileInstall

Or drive it directly from Python (what an MCP client effectively does under the
hood, minus the JSON-RPC framing)::

    from duho.mcp import describe_tools, call_tool
    from mcp_app import FileInstall

    tools = describe_tools(FileInstall)
    # -> [{"name": "FileInstall", ...}, {"name": "FileInstall.install", ...}]

    result = call_tool(
        FileInstall, "FileInstall.install", {"source": "a.txt", "destination": "b.txt"}
    )
    # -> {"content": [{"type": "text", "text": "...logged install plan..."}]}

**Wiring into an MCP client.** Any client that speaks the MCP stdio transport
(newline-delimited JSON-RPC 2.0 over stdin/stdout) can launch this as a server
subprocess. A typical client config entry looks like::

    {
      "mcpServers": {
        "duho-fileinstall": {
          "command": "python",
          "args": ["-m", "duho.mcp", "mcp_app:FileInstall"],
          "cwd": "/path/to/duho/examples"
        }
      }
    }

The client then handles the ``initialize`` handshake, calls ``tools/list`` to
discover ``FileInstall``/``FileInstall.install`` (with real JSON-Schema
``inputSchema``s built straight from ``Install``'s own field declarations), and
issues ``tools/call`` requests -- no separate MCP-facing code to write or keep in
sync with the CLI.

Note: ``call_tool`` captures ``sys.stdout`` only (Decision 4's return
convention), not logging output -- ``Install.__call__`` reports via
``self._logger_.info(...)``, so a ``tools/call`` against it returns a success
result with an EMPTY text block (a logging-only command has nothing on stdout;
that is the honest, documented behavior, not a bug). A command that wants
visible ``tools/call`` output should ``print(...)`` it (like the schema
docstring's own worked example implies) or return a JSON-serialisable value.
"""
import sys

import duho
import duho.mcp

from fileinstall import FileInstall

if __name__ == "__main__":
    # `python -m duho.mcp mcp_app:FileInstall` is the normal way to run this as
    # an MCP server; this direct entry point is a convenience for `python
    # examples/mcp_app.py` (equivalent to `python -m duho.mcp mcp_app:FileInstall`
    # with argv already fixed).
    sys.exit(duho.mcp.serve(FileInstall))
