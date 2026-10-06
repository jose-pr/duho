"""A command served over MCP sees the same instance variables as on the CLI."""

import json

from duho import Cli, Cmd
from duho.mcp import call_tool


class Show(Cmd):
    """Return the instance's own variables."""

    def __call__(self):
        return sorted(vars(self))


class Root(Cli):
    """Root."""

    _parsername_ = "root"
    _subcommands_ = [Show]


def test_framework_marker_is_not_left_in_the_instance():
    result = call_tool(Root, "root.show", {})
    names = json.loads(result["content"][0]["text"])
    assert not [n for n in names if "mcp" in n]
