"""Smoke tests for examples/dotagents.py, examples/fileinstall.py, examples/mcp_app.py.

These exercise the example files as acceptance tests for duho's public API
surface: LoggingArgs, _subcommands_, Cmd dispatch via duho.main(), and
(for fileinstall) positionals, Union types, NS(nargs="?"), a custom
action=UpdateAction, and NS(conflicts=...) mutually-exclusive grouping. The
mcp_app tests exercise duho.mcp's describe_tools/call_tool against a real,
unmodified duho CLI (fileinstall.FileInstall), the point of that example.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))

import duho
import duho.mcp

import dotagents
import fileinstall
import mcp_app


def test_dotagents_install_parses_fields():
    result = duho.parse(dotagents.Install, ["--dest", "/tmp/x", "--dry-run"])
    assert result.dest == Path("/tmp/x")
    assert result.dry_run is True


def test_dotagents_main_install_dry_run_returns_0():
    assert duho.main(dotagents.Dotagents, ["install", "--dry-run"]) == 0


def test_fileinstall_install_positionals_are_path():
    result = duho.parse(fileinstall.Install, ["src", "dst"])
    assert isinstance(result.source, Path)
    assert isinstance(result.destination, Path)
    assert result.source == Path("src")
    assert result.destination == Path("dst")


def test_fileinstall_install_type_flag():
    result = duho.parse(fileinstall.Install, ["--type", "dir", "src", "dst"])
    assert result.type is fileinstall.FileType.dir


def test_fileinstall_install_type_flag_str_fallback():
    result = duho.parse(fileinstall.Install, ["--type", "custom", "src", "dst"])
    assert result.type == "custom"


def test_fileinstall_install_options_update_action():
    result = duho.parse(fileinstall.Install, ["-O", "a=1", "-O", "b=2", "src", "dst"])
    assert result.options == {"a": "1", "b": "2"}


def test_fileinstall_main_install_returns_0():
    assert duho.main(fileinstall.FileInstall, ["install", "src", "dst"]) == 0


def test_mcp_app_describes_fileinstall_as_tools():
    tools = duho.mcp.describe_tools(mcp_app.FileInstall)
    names = {t["name"] for t in tools}
    assert names == {"FileInstall", "FileInstall.install"}
    install = next(t for t in tools if t["name"] == "FileInstall.install")
    assert "source" in install["inputSchema"]["properties"]
    assert "destination" in install["inputSchema"]["properties"]


def test_mcp_app_call_tool_dispatches_install():
    result = duho.mcp.call_tool(
        mcp_app.FileInstall,
        "FileInstall.install",
        {"source": "a.txt", "destination": "b.txt"},
    )
    assert result.get("isError") is not True
