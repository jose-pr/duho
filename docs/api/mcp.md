# MCP

Opt-in MCP (Model Context Protocol) tool surface over a duho CLI — a static
`_subcommands_` class tree, or a full `duho.app()`-built tree (class AND
module commands) — via `describe_tools`/`call_tool`, and a stdio server
runnable as `python -m duho.mcp <app>`. `import duho.mcp` to use it; core
`duho` never imports this module, and neither does a normal
`duho.main`/`duho.app` run — the `<PREFIX>MCP`/`<NAME>_MCP` launch trigger
(default on; opt out with `_mcp_ = False`/`app(mcp=False)`) and the opt-in
`McpCmd`/`_mcp_command_`/`app(mcp_command=...)` subcommand (registered under
either `duho.main` or `duho.app` — `_mcp_command_` reads the same way from
both) both import it lazily, only once actually triggered. A non-root
command opts OUT of the tool surface entirely with its own
`_mcp_ = False` (a module command via a module-level `_mcp_ = False`) —
excluded together with its whole subtree, refused the same way an unknown
tool name is.

Tools that are not commands (forwarded from another server, or built at
run time) come from a `ToolProvider`: pass a factory as `serve(..., tools=)`
or `serve_running_app(tools=)`, or declare it on the root so every server of
the app uses it:

```python
class App(Cli):
    _mcp_tools_ = staticmethod(lambda notify: Forwarder(notify))
```

The factory gets `notify(method, params=None)`, which writes a notification
such as `notifications/tools/list_changed` on the protocol stream. The
provider is asked for a name only when no command tool has it, and a provider
tool named like a command tool is left out.

::: duho.mcp
