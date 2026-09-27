# MCP

Opt-in MCP (Model Context Protocol) tool surface over a duho CLI — a static
`_subcommands_` class tree, or a full `duho.app()`-built tree (class AND
module commands) — via `describe_tools`/`call_tool`, and a stdio server
runnable as `python -m duho.mcp <app>`. `import duho.mcp` to use it; core
`duho` never imports this module, and neither does a normal
`duho.main`/`duho.app` run — the `<PREFIX>MCP`/`<NAME>_MCP` launch trigger
(default on; opt out with `_mcp_ = False`/`app(mcp=False)`) and the opt-in
`McpCmd`/`_mcp_command_`/`app(mcp_command=...)` subcommand both import it
lazily, only once actually triggered.

::: duho.mcp
