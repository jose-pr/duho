from __future__ import annotations

import typing as _ty


class ToolProvider(_ty.Protocol):
    """Tools an application serves beside its command tools.

    :func:`serve` builds one per connection from a factory (its ``tools``
    argument, else the root's ``_mcp_tools_``), calls it from the loop thread
    only, one request at a time, and calls :meth:`close` when the loop ends.
    """

    def list_tools(self) -> _ty.List[dict]:
        """The provider's MCP ``Tool`` objects, read on every ``tools/list``."""
        ...

    def call_tool(self, name: str, arguments: dict) -> _ty.Optional[dict]:
        """The ``CallToolResult`` for ``name``, or ``None`` when it is not ours."""
        ...

    def close(self) -> None:
        """Release what the provider holds; called once."""
        ...
