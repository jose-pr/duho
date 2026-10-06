"""An application has one name on every surface: the usage line, the
completion script, the ``<NAME>_MCP`` variable, MCP tool names and the default
logger. It never depends on how the program was launched.
"""

import io
import json
import logging
import subprocess
import sys
import types

import pytest

import duho
from conftest import subprocess_env
from duho import Cli, Cmd, LoggingArgs
from duho.args import _app_name, _mcp_env_var_name
from duho.mcp import _core_for_app, describe_tools


class Leaf(Cmd):
    """A plain command."""

    def __call__(self):
        return 0


class LoggingLeaf(LoggingArgs, Cmd):
    """A command that is itself a LoggingArgs."""

    def __call__(self):
        return 0


def _bare_main_module(monkeypatch, spec_name=None):
    """Make ``__main__`` look like a script run directly, or like
    ``python -m <spec_name>``."""
    module = types.ModuleType("__main__")
    if spec_name is not None:
        module.__spec__ = types.SimpleNamespace(name=spec_name)
    monkeypatch.setitem(sys.modules, "__main__", module)


# --------------------------------------------------------------------------
# _app_name precedence, one test per rule
# --------------------------------------------------------------------------


def test_explicit_name_wins_over_everything():
    class Declared(Cli):
        _parsername_ = "forge"

    assert _app_name(Declared, "other") == "other"


def test_own_parsername_comes_before_the_package():
    class Declared(Cli):
        _parsername_ = "forge"

    assert _app_name(Declared) == "forge"


def test_an_inherited_parsername_is_not_the_apps_name():
    class Base(Cli):
        _parsername_ = "base-name"

    class Child(Base):
        pass

    assert _app_name(Child) == "test_app_name"


def test_top_level_package_comes_before_the_class_name():
    class MyTool(Cli):
        pass

    MyTool.__module__ = "pkgforge.cli.deep"
    assert _app_name(MyTool) == "pkgforge"


def test_a_script_run_directly_is_named_after_its_class(monkeypatch):
    class PkgForge(Cli):
        pass

    PkgForge.__module__ = "__main__"
    _bare_main_module(monkeypatch)
    assert _app_name(PkgForge) == "pkg-forge"


def test_python_dash_m_names_the_package_of_the_main_module(monkeypatch):
    class PkgForge(Cli):
        pass

    PkgForge.__module__ = "__main__"
    _bare_main_module(monkeypatch, spec_name="pkgforge.__main__")
    assert _app_name(PkgForge) == "pkgforge"


def test_a_class_defined_in_duho_is_never_named_duho():
    assert _app_name(duho.Args) == "args"


def test_no_class_is_named_app():
    assert _app_name(None) == "app"


@pytest.mark.parametrize("argv", [["/bin/some-script.py"], ["x/__main__.py"], []])
def test_argv0_never_names_the_app(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    assert _app_name(Leaf) == "test_app_name"


# --------------------------------------------------------------------------
# The same name from every launch, on every surface
# --------------------------------------------------------------------------

_CLI_SOURCE = '''\
import io
import json
import logging

import duho
from duho import Cli, Cmd, LoggingArgs
from duho.args import _mcp_env_var_name
from duho.mcp import describe_tools


class Scan(Cmd):
    """Scan."""

    def __call__(self):
        return 0


class Build(LoggingArgs, Cmd):
    """Build."""

    def __call__(self):
        return 0


class PkgForge(LoggingArgs, Cli):
    """Root."""

    _completion_ = True
    _subcommands_ = [Scan, Build]


def report(cls=PkgForge):
    buf = io.StringIO()
    duho.print_completion(cls, "bash", file=buf)
    bound = buf.getvalue().splitlines()[-1].rsplit(" ", 1)[-1]
    levels = {}
    for leaf in ("scan", "build"):
        logging.getLogger("APP").setLevel(logging.NOTSET)
        duho.main(cls, ["-v", leaf], setup_logging=True)
        levels[leaf] = logging.getLogger("APP").level
    print(
        json.dumps(
            {
                "prog": cls._parser_().prog,
                "completion": bound,
                "mcp_var": _mcp_env_var_name(cls),
                "tool_root": describe_tools(cls)[0]["name"].split(".")[0],
                "debug_on_app_logger": levels,
            }
        )
    )
'''

_MAIN_SOURCE = """\
from pkgforge.cli import report

report()
"""


@pytest.fixture
def forge_project(tmp_path):
    pkg = tmp_path / "pkgforge"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "cli.py").write_text(_CLI_SOURCE.replace("APP", "pkgforge"), "utf-8")
    (pkg / "__main__.py").write_text(_MAIN_SOURCE, encoding="utf-8")
    # The root class itself lives in the `-m` entry module.
    inline = tmp_path / "pkginline"
    inline.mkdir()
    (inline / "__init__.py").write_text("")
    (inline / "__main__.py").write_text(
        _CLI_SOURCE.replace("APP", "pkginline") + "\nreport()\n", encoding="utf-8"
    )
    (tmp_path / "app.py").write_text(_MAIN_SOURCE, encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "run.py").write_text(_MAIN_SOURCE, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "launch, package",
    [
        (["app.py"], "pkgforge"),
        (["tools/run.py"], "pkgforge"),
        (["-m", "pkgforge"], "pkgforge"),
        (["-m", "pkginline"], "pkginline"),
    ],
    ids=["script", "script-in-subdir", "python-dash-m", "dash-m-defines-the-root"],
)
def test_every_surface_names_the_package_whatever_the_launch(
    forge_project, launch, package
):
    proc = subprocess.run(
        [sys.executable, *launch],
        cwd=str(forge_project),
        env=subprocess_env(extra_path=forge_project),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.splitlines()[-1])
    assert got == {
        "prog": package,
        "completion": package,
        "mcp_var": package.upper() + "_MCP",
        "tool_root": package,
        "debug_on_app_logger": {"scan": 10, "build": 10},
    }


# --------------------------------------------------------------------------
# A declared name, and app(name=) over it
# --------------------------------------------------------------------------


class _Declared(Cli):
    """Declares its own name."""

    _parsername_ = "forge"
    _completion_ = True
    _subcommands_ = [Leaf]


def test_a_declared_name_is_used_on_every_surface():
    assert _Declared._parser_().prog == "forge"
    assert _mcp_env_var_name(_Declared) == "FORGE_MCP"
    assert describe_tools(_Declared)[0]["name"] == "forge.leaf"
    buf = io.StringIO()
    duho.print_completion(_Declared, "bash", file=buf)
    assert buf.getvalue().splitlines()[-1].endswith(" forge")


def test_app_name_argument_beats_a_declared_name(capsys):
    with pytest.raises(SystemExit):
        duho.app(_Declared, name="other", argv=["--print-completion", "bash"])
    assert capsys.readouterr().out.splitlines()[-1].endswith(" other")
    assert _mcp_env_var_name(_Declared, name="other") == "OTHER_MCP"
    core = _core_for_app(_Declared, commands=[Leaf], name="other", argv=[])
    assert describe_tools(core)[0]["name"] == "other.leaf"


# --------------------------------------------------------------------------
# Which logger -v raises
# --------------------------------------------------------------------------


def _debug_loggers(root, leaf, candidates):
    for name in candidates:
        logging.getLogger(name).setLevel(logging.NOTSET)
    try:
        duho.main(root, ["-v", leaf], setup_logging=True)
        return [n for n in candidates if logging.getLogger(n).level == logging.DEBUG]
    finally:
        for name in candidates:
            logging.getLogger(name).setLevel(logging.NOTSET)


@pytest.fixture
def _quiet_root_logger():
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    root.handlers[:] = []
    yield
    root.handlers[:] = saved
    root.setLevel(level)


class _AppLogRoot(LoggingArgs, Cli):
    """An unnamed root."""

    _subcommands_ = [Leaf, LoggingLeaf]


class _CustomLogRoot(_AppLogRoot):
    """A root that names the logger."""

    _logger_name_ = "test_app_name.custom"


_CANDIDATES = ("test_app_name", "test_app_name.custom", "leaf", "logging-leaf")


@pytest.mark.parametrize("leaf", ["leaf", "logging-leaf"])
def test_verbose_raises_the_application_logger(_quiet_root_logger, leaf):
    assert _debug_loggers(_AppLogRoot, leaf, _CANDIDATES) == ["test_app_name"]


@pytest.mark.parametrize("leaf", ["leaf", "logging-leaf"])
def test_a_root_logger_name_reaches_every_command(_quiet_root_logger, leaf):
    assert _debug_loggers(_CustomLogRoot, leaf, _CANDIDATES) == ["test_app_name.custom"]


# --------------------------------------------------------------------------
# The MCP subcommand does not change what the app is
# --------------------------------------------------------------------------


def test_auto_version_with_an_mcp_command_does_not_report_duhos_version(capsys):
    class Versioned(Cli):
        _version_ = duho.AUTO
        _mcp_command_ = True
        _subcommands_ = [Leaf]

    Versioned.__module__ = "an_uninstalled_package"
    with pytest.raises(SystemExit) as exc:
        duho.main(Versioned, ["--version"], setup_logging=False)
    assert exc.value.code == 2
    assert duho.__version__ not in capsys.readouterr().out
