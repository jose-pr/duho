"""A tool below a command with an optional positional works without supplying it."""

import pytest

from duho import Cli, Cmd
from duho.mcp import call_tool


class Safe(Cmd):
    """Safe."""

    name: str = "n"
    "name"
    ("name",)

    def __call__(self):
        return {"ran": "Safe", "name": self.name}


class Deploy(Cmd):
    """Deploy."""

    def __call__(self):
        return {"ran": "Deploy"}


class Staging(Cli):
    """Staging."""

    _subcommands_ = [Deploy]


class Root(Cli):
    """Root."""

    _parsername_ = "root"

    path: str = "."
    "optional positional"
    ("path",)

    _subcommands_ = [Safe, Staging]


@pytest.mark.parametrize(
    "tool,arguments,ran",
    [
        ("root.staging.deploy", {}, "Deploy"),
        ("root.safe", {"name": "zzz"}, "Safe"),
        ("root.safe", {}, "Safe"),
    ],
)
def test_tool_runs_without_the_ancestors_optional_positional(tool, arguments, ran):
    result = call_tool(Root, tool, arguments)
    assert "isError" not in result, result
    assert ran in result["content"][0]["text"]


def test_supplied_ancestor_positional_still_wins():
    result = call_tool(Root, "root.staging.deploy", {"path": "elsewhere"})
    assert "isError" not in result, result
