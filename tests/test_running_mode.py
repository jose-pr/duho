"""``duho.running_mode``: ``"mcp"`` only while a tool call dispatches a command."""

import pytest

import duho
from duho import Cli, Cmd
from duho.mcp import call_tool

seen = []


class Probe(Cmd):
    _parsername_ = "probe"

    def __call__(self):
        seen.append(duho.running_mode())
        return 0


class AsyncProbe(Cmd):
    _parsername_ = "async-probe"

    async def __call__(self):
        seen.append(duho.running_mode())
        return 0


class Boom(Cmd):
    _parsername_ = "boom"

    def __call__(self):
        seen.append(duho.running_mode())
        raise RuntimeError("broke")


class Root(Cli):
    _parsername_ = "tool-call-root"
    _subcommands_ = [Probe, AsyncProbe, Boom]


@pytest.fixture(autouse=True)
def _reset():
    seen.clear()


def test_cli_outside_any_call():
    assert duho.running_mode() == "cli"


def test_cli_under_main():
    assert duho.main(Root, ["probe"], setup_logging=False) == 0
    assert seen == ["cli"]


def test_mcp_inside_a_command_run_by_call_tool():
    result = call_tool(Root, "tool-call-root.probe", {})
    assert result.get("isError") is not True
    assert seen == ["mcp"]
    assert duho.running_mode() == "cli"


def test_mcp_inside_an_async_command():
    result = call_tool(Root, "tool-call-root.async-probe", {})
    assert result.get("isError") is not True
    assert seen == ["mcp"]
    assert duho.running_mode() == "cli"


def test_cli_again_after_a_call_that_raised():
    result = call_tool(Root, "tool-call-root.boom", {})
    assert result.get("isError") is True
    assert seen == ["mcp"]
    assert duho.running_mode() == "cli"


def test_it_is_a_root_export():
    assert "running_mode" in duho.__all__
