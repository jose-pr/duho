"""Dev-only conformance test: the official MCP SDK's own client against a
real ``duho.mcp`` server.

``duho.mcp`` stays a zero-dependency stdlib implementation on the 3.9 floor;
the SDK itself is 3.10+-only and never a runtime or ``duho[mcp]``
dependency. It is used HERE, and only here, as a dev-only conformance
ORACLE -- driving a real duho MCP server over stdio through the SDK's own
``ClientSession`` proves duho's hand-rolled protocol implementation is
something the official client actually accepts, without duho depending on
the SDK to do it.

``pytest.importorskip`` skips this whole module cleanly on 3.9 (where the
SDK cannot even be installed) and on any interpreter where the ``mcp[dev]``
extra was not installed.
"""

import sys

import pytest

mcp_sdk = pytest.importorskip(
    "mcp",
    reason="dev-only MCP SDK conformance oracle -- `pip install duho[dev]` on 3.10+",
)

import asyncio  # noqa: E402

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402

from conftest import subprocess_env  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10), reason="mcp SDK requires Python 3.10+"
)

# A class command (`Ping`) plus one discovered MODULE command (`cmds/greet.py`)
# -- the plan's own "a class tree plus one module command" shape -- served
# over stdio via the `<NAME>_MCP` launch trigger, exactly like a real app.
_RUNNER = '''\
import pathlib
import sys
from duho import Cli, Cmd
from duho.runtime import app


class Ping(Cmd):
    """Reply pong."""

    def __call__(self):
        print("pong")
        return 0


class Root(Cli):
    """SDK conformance test root."""

    _subcommands_ = [Ping]


if __name__ == "__main__":
    sys.exit(
        app(Root, source=pathlib.Path(__file__).parent / "cmds", argv=sys.argv[1:])
    )
'''

_GREET_MODULE = '''\
"""Print a greeting (module command)."""


def main(args=None):
    print("hello from module command")
    return 0
'''


def _write(dir_path, name, source):
    path = dir_path / name
    path.write_text(source, encoding="utf-8")
    return path


def test_duho_mcp_server_satisfies_the_official_sdk_client(tmp_path):
    """Initialize, list tools, call a class-command tool AND a module-
    command tool, then call an unknown tool and expect a protocol-level
    error -- all through the real SDK client, which validates every
    response against its own models. A schema violation in duho's hand
    rolled JSON surfaces here as a client-side validation failure, not a
    silent pass.
    """
    cmds_dir = tmp_path / "cmds"
    cmds_dir.mkdir()
    _write(cmds_dir, "greet.py", _GREET_MODULE)
    runner = _write(tmp_path, "runner.py", _RUNNER)

    env = subprocess_env()
    env["RUNNER_MCP"] = "stdio"

    async def drive():
        params = StdioServerParameters(
            command=sys.executable, args=[str(runner)], env=env
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init_result = await session.initialize()
                # The served app's own identity, not a fixed "duho.mcp" --
                # Root has no declared _version_, so item 8's empty-string
                # contract applies here too (never duho's own version).
                assert init_result.server_info.name == "root"
                assert init_result.server_info.version == ""

                tools_result = await session.list_tools()
                names = {t.name for t in tools_result.tools}
                assert names == {"root.ping", "root.greet"}

                ping_result = await session.call_tool("root.ping", {})
                assert ping_result.is_error is not True
                ping_text = "".join(getattr(c, "text", "") for c in ping_result.content)
                assert "pong" in ping_text

                greet_result = await session.call_tool("root.greet", {})
                assert greet_result.is_error is not True
                greet_text = "".join(
                    getattr(c, "text", "") for c in greet_result.content
                )
                assert "hello from module command" in greet_text

                # An unknown tool name is a JSON-RPC protocol error
                # (duho.mcp.UnknownToolError, code -32602) -- the SDK must
                # surface it as a protocol-level McpError, never silently
                # as a successful (even if isError-flagged) tool result.
                with pytest.raises(MCPError):
                    await session.call_tool("root.does-not-exist", {})

    asyncio.run(drive())
