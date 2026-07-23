"""Smoke tests for examples/dotagents.py, examples/fileinstall.py, examples/mcp_app.py,
examples/discovery_app.py, examples/runpath_app.py.

These exercise the example files as acceptance tests for duho's public API
surface: LoggingArgs, _subcommands_, Cmd dispatch via duho.main(), and
(for fileinstall) positionals, Union types, NS(nargs="?"), a custom
action=UpdateAction, and NS(conflicts=...) mutually-exclusive grouping. The
mcp_app tests exercise duho.mcp's describe_tools/call_tool against a real,
unmodified duho CLI (fileinstall.FileInstall), the point of that example.
discovery_app/runpath_app exercise duho.discover_commands / duho.runpath end
to end against real files on disk (examples/discovery_cmds/, examples/rc/),
not synthetic tmp_path fixtures.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))

import duho
import duho.mcp
import duho.runpath
from duho.discovery import CmdBuilder, discover_commands

import discovery_app
import dotagents
import fileinstall
import mcp_app

_EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


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


def test_discovery_app_finds_module_and_class_commands():
    commands = discover_commands(_EXAMPLES_DIR / "discovery_cmds")
    names = set()
    for command in commands:
        names.add(getattr(command, "_parsername_", None))
    assert names >= {"greet", "status", "whoami"}


def test_discovery_app_greet_module_command_runs(capsys):
    # Passing DiscoveryAppArgs as root is REQUIRED here: without it args is a
    # bare Args with no _logger_ (greet.py's module command calls
    # args._logger_.debug(...), which only resolves because the parsed
    # instance actually IS a DiscoveryAppArgs -- data AND methods, since a
    # module command's `args` parameter is the parsed root instance itself,
    # unlike a provider-built RunPathCmd (see duho.runpath.register(base=...)).
    exit_code = duho.app(
        discovery_app.DiscoveryAppArgs,
        commands=discover_commands(_EXAMPLES_DIR / "discovery_cmds"),
        name="discovery-app",
        argv=["greet", "World", "--shout"],
    )
    assert exit_code == 0
    assert "HELLO, WORLD!" in capsys.readouterr().out


def test_discovery_app_whoami_class_command_runs(capsys):
    exit_code = duho.app(
        discovery_app.DiscoveryAppArgs,
        commands=discover_commands(_EXAMPLES_DIR / "discovery_cmds"),
        name="discovery-app",
        argv=["whoami"],
    )
    assert exit_code == 0
    assert "discovery-app" in capsys.readouterr().out


def test_runpath_app_rc_resolves_via_cmdbuilder():
    command = CmdBuilder("rc", _EXAMPLES_DIR / "rc").command
    assert command._parsername_ == "rc"


def test_runpath_app_rc_runs_all_steps_in_order(capsys):
    command = CmdBuilder("rc", _EXAMPLES_DIR / "rc").command
    exit_code = duho.app(commands=[command], name="runpath-app", argv=["rc"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "checking prerequisites" in out
    assert "provisioning" in out
    assert "emailing a status report" in out


def test_runpath_app_rcopts_selects_one_step(capsys):
    command = CmdBuilder("rc", _EXAMPLES_DIR / "rc").command
    exit_code = duho.app(
        commands=[command],
        name="runpath-app",
        argv=["rc", "--rcopts", "!*,provision"],
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "provisioning" in out
    assert "checking prerequisites" not in out


def test_runpath_app_logger_and_shared_root_label_work(caplog):
    # Regression: a bare provider-built RunPathCmd used to have no real
    # _logger_/_set_loglevels_ (see duho.runpath.register(base=...)) -- -v
    # never activated logging and every logger.info() in __main__.py/steps
    # silently vanished. RunpathAppArgs (LoggingArgs-based) is passed as
    # root here, and __main__.py logs via format_tag_line(cmd, ...), which
    # reads cmd.label -- both the logging AND the shared-root-field wiring
    # are exercised by this one assertion.
    import runpath_app

    command = CmdBuilder("rc", _EXAMPLES_DIR / "rc").command
    with caplog.at_level("INFO", logger="rc"):
        exit_code = duho.app(
            runpath_app.RunpathAppArgs,
            commands=[command],
            name="runpath-app",
            argv=["--label", "test-label", "rc"],
        )
    assert exit_code == 0
    messages = [rec.message for rec in caplog.records]
    assert any("[test-label]" in message for message in messages)
