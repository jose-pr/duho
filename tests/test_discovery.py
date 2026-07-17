"""Tests for duho.discovery: Command protocol, ModuleCommand, CmdBuilder, and
the resilient discover_commands walker.

All fixtures are REAL ``.py`` files written under ``tmp_path`` (never ``python
-c`` -- ``Cmd`` subclasses defined in a ``-c`` string get no AST-derived flags/
docstrings, per the project's AST/-c limitation). Each test builds exactly the
command files it needs so the fixtures double as readable documentation of the
supported shapes.
"""

import sys
import textwrap

import pytest

import duho
from duho import Cmd
from duho.discovery import (
    CmdBuilder,
    Command,
    ModuleCommand,
    discover_commands,
    is_class_command,
    is_module_command,
    register_command_provider,
)
from duho import discovery as _discovery


# --------------------------------------------------------------------------
# Fixture-file helpers
# --------------------------------------------------------------------------

# Reusable source snippets for command files.

_CLASS_CMD_DEPLOY = '''\
"""Deploy the thing."""
import duho
from duho import Cmd


class Deploy(Cmd):
    """Deploy the thing to an environment."""

    env: str = "prod"
    "Target environment"
    ("--env",)

    def main(self):
        return "deployed " + self.env
'''

_CLASS_CMD_STATUS = '''\
"""Show status."""
import duho
from duho import Cmd


class Status(Cmd):
    """Show status."""

    def main(self):
        return "status ok"
'''

# A module command: top-level ``main`` is the entrypoint (no Cmd subclass).
_MODULE_CMD = '''\
"""Run a module-style command."""


def main(args=None):
    return "module ran"
'''

# A module command using the ``run`` fallback entrypoint.
_MODULE_CMD_RUN = '''\
"""A module command via the run() fallback."""


def run(args=None):
    return "run fallback"
'''

# A helpers-only file: no Cmd subclass, no entrypoint -> contributes nothing.
_HELPERS = '''\
"""Just helpers, not a command."""


class NotACommand:
    pass


def helper():
    return 1
'''

# A file that re-imports another module's Cmd subclass unchanged: the
# __module__ dedup filter must NOT collect it here.
_REEXPORT = '''\
"""Re-exports Deploy from the deploy module -- must be deduped out."""
from deploy import Deploy  # noqa: F401
'''

# A file with two Cmd subclasses in one module -> both collected.
_MULTI = '''\
"""Two commands in one file."""
from duho import Cmd


class Alpha(Cmd):
    """Alpha command."""

    def main(self):
        return "a"


class Beta(Cmd):
    """Beta command."""

    def main(self):
        return "b"
'''

# A file importing a nonexistent optional dependency -> ImportError -> skipped.
_MISSING_DEP = '''\
"""Command needing an optional dep that is not installed."""
import duho_totally_missing_optional_dep  # noqa: F401
from duho import Cmd


class Needy(Cmd):
    def main(self):
        return "needy"
'''

# A file with a real syntax error -> discovery must NOT swallow it.
_SYNTAX_ERROR = '''\
"""Broken command file."""
from duho import Cmd


class Broken(Cmd)      # <- missing colon: SyntaxError
    def main(self):
        return "broken"
'''


def _write(directory, name, source):
    path = directory / name
    path.write_text(textwrap.dedent(source))
    return path


@pytest.fixture
def flat_cmds(tmp_path):
    """A directory of loose command ``.py`` files (no package __init__)."""
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    _write(tmp_path, "status.py", _CLASS_CMD_STATUS)
    _write(tmp_path, "runme.py", _MODULE_CMD)
    _write(tmp_path, "_helpers.py", _HELPERS)
    return tmp_path


@pytest.fixture
def package_cmds(tmp_path, monkeypatch):
    """A real importable package ``pkg_under_test.cmds`` on sys.path."""
    pkg = tmp_path / "pkg_under_test"
    cmds = pkg / "cmds"
    cmds.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (cmds / "__init__.py").write_text("")
    _write(cmds, "deploy.py", _CLASS_CMD_DEPLOY)
    _write(cmds, "status.py", _CLASS_CMD_STATUS)
    _write(cmds, "runme.py", _MODULE_CMD)
    _write(cmds, "_helpers.py", _HELPERS)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield "pkg_under_test.cmds"
    # Drop imported submodules so a later test's identically-named package does
    # not resolve to this run's modules.
    for name in list(sys.modules):
        if name == "pkg_under_test" or name.startswith("pkg_under_test."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def _restore_providers():
    """Snapshot/restore the global provider registry around each test."""
    saved = list(_discovery._PROVIDERS)
    try:
        yield
    finally:
        _discovery._PROVIDERS[:] = saved


def _names(commands):
    return [c._parsername_ if is_module_command(c)
            else (getattr(c, "_parsername_", None) or c.__name__)
            for c in commands]


# --------------------------------------------------------------------------
# Predicates + protocol
# --------------------------------------------------------------------------


def test_is_class_command_predicate():
    class MyCmd(Cmd):
        def main(self):
            return 0

    assert is_class_command(MyCmd)
    assert not is_class_command(Cmd)  # the base itself is excluded
    assert not is_class_command(duho.Args)
    assert not is_class_command(object)
    assert not is_class_command(42)


def test_class_command_satisfies_protocol():
    class MyCmd(Cmd):
        def main(self):
            return 0

    # runtime_checkable Protocol: a Cmd subclass has _parsername_ (once parsed
    # or via the class rule) and main.
    assert hasattr(MyCmd, "main")


# --------------------------------------------------------------------------
# ModuleCommand wrapper
# --------------------------------------------------------------------------


def test_module_command_wraps_main_entrypoint(tmp_path):
    path = _write(tmp_path, "runme.py", _MODULE_CMD)
    builder = CmdBuilder("runme", path)
    cmd = builder.command
    assert isinstance(cmd, ModuleCommand)
    assert is_module_command(cmd)
    assert cmd._parsername_ == "runme"
    assert cmd.help == "Run a module-style command."
    assert cmd.main() == "module ran"
    assert cmd() == "module ran"


def test_module_command_run_fallback(tmp_path):
    path = _write(tmp_path, "viarun.py", _MODULE_CMD_RUN)
    cmd = CmdBuilder("viarun", path).command
    assert isinstance(cmd, ModuleCommand)
    assert cmd.main() == "run fallback"


def test_module_command_name_normalizes_underscores(tmp_path):
    path = _write(tmp_path, "deploy_all.py", _MODULE_CMD)
    cmd = CmdBuilder("deploy_all", path).command
    assert cmd._parsername_ == "deploy-all"


def test_module_command_name_override(tmp_path):
    src = _MODULE_CMD.replace(
        '"""Run a module-style command."""',
        '"""Run a module-style command."""\n_parsername_ = "custom-name"',
    )
    path = _write(tmp_path, "whatever.py", src)
    cmd = CmdBuilder("whatever", path).command
    assert cmd._parsername_ == "custom-name"


def test_module_without_entrypoint_is_not_a_command(tmp_path):
    path = _write(tmp_path, "helpers.py", _HELPERS)
    with pytest.raises(NotImplementedError):
        CmdBuilder("helpers", path)


def test_module_command_default_lifecycle_hooks(tmp_path):
    path = _write(tmp_path, "runme.py", _MODULE_CMD)
    cmd = CmdBuilder("runme", path).command
    # All hooks present and callable; defaults are no-ops / None-returning init.
    assert cmd.init(object()) is None
    assert cmd.register(object(), object()) is None
    assert cmd.success(None, object()) is None
    assert cmd.finally_(None, object()) is None


def test_module_command_uses_defined_hooks(tmp_path):
    src = _MODULE_CMD + textwrap.dedent(
        '''
        def init(args=None):
            return {"ctx": 1}

        def success(ctx, args=None):
            return "ok"
        '''
    )
    path = _write(tmp_path, "hooked.py", src)
    cmd = CmdBuilder("hooked", path).command
    assert cmd.init() == {"ctx": 1}
    assert cmd.success({"ctx": 1}) == "ok"


def test_module_command_logger_from_args_instance(tmp_path):
    path = _write(tmp_path, "runme.py", _MODULE_CMD)
    cmd = CmdBuilder("runme", path).command

    class WithLogger:
        _logger_ = duho.logging.getLogger("some.scoped.logger")

    resolved = cmd._logger_for(WithLogger())
    assert resolved.name == "some.scoped.logger"
    # Falls back to the "duho" logger when the args instance has no _logger_.
    assert cmd._logger_for(object()).name == "duho"


# --------------------------------------------------------------------------
# discover_commands -- package form
# --------------------------------------------------------------------------


def test_discover_from_package(package_cmds):
    commands = discover_commands(package_cmds)
    names = _names(commands)
    # Deploy + Status (class commands), runme (module command). _helpers skipped.
    assert names == ["Deploy", "Status", "runme"]
    # Sorted deterministically.
    assert names == sorted(names)


def test_discover_from_package_module_not_package_raises(tmp_path, monkeypatch):
    # A plain module (no __path__) is not a package.
    _write(tmp_path, "lonely.py", _CLASS_CMD_STATUS)
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with pytest.raises(ImportError):
            discover_commands("lonely")
    finally:
        sys.modules.pop("lonely", None)


# --------------------------------------------------------------------------
# discover_commands -- path form (Path and str)
# --------------------------------------------------------------------------


def test_discover_from_path_object(flat_cmds):
    commands = discover_commands(flat_cmds)
    assert _names(commands) == ["Deploy", "Status", "runme"]


def test_discover_from_path_string(flat_cmds):
    commands = discover_commands(str(flat_cmds))
    assert _names(commands) == ["Deploy", "Status", "runme"]


def test_path_and_package_forms_agree(flat_cmds, package_cmds):
    from_path = _names(discover_commands(flat_cmds))
    from_pkg = _names(discover_commands(package_cmds))
    assert from_path == from_pkg == ["Deploy", "Status", "runme"]


def test_underscore_files_skipped(tmp_path):
    _write(tmp_path, "real.py", _CLASS_CMD_STATUS)
    _write(tmp_path, "_private.py", _CLASS_CMD_DEPLOY)
    _write(tmp_path, "__init__.py", _CLASS_CMD_DEPLOY)
    commands = discover_commands(tmp_path)
    assert _names(commands) == ["Status"]


# --------------------------------------------------------------------------
# multi-command module, empty module, dedup
# --------------------------------------------------------------------------


def test_multiple_commands_per_module(tmp_path):
    _write(tmp_path, "multi.py", _MULTI)
    commands = discover_commands(tmp_path)
    assert _names(commands) == ["Alpha", "Beta"]


def test_module_with_both_class_and_module_command(tmp_path):
    src = _CLASS_CMD_DEPLOY + textwrap.dedent(
        '''

        def main(args=None):
            return "module entry"
        '''
    )
    _write(tmp_path, "both.py", src)
    commands = discover_commands(tmp_path)
    names = sorted(_names(commands))
    # One class command (Deploy) + one module command (stem "both").
    assert names == ["Deploy", "both"]


def test_empty_module_contributes_nothing(tmp_path):
    _write(tmp_path, "empty.py", _HELPERS)
    _write(tmp_path, "real.py", _CLASS_CMD_STATUS)
    commands = discover_commands(tmp_path)
    assert _names(commands) == ["Status"]


def test_reexported_class_is_deduped(tmp_path):
    # deploy.py defines Deploy; reexport.py imports it unchanged. The
    # __module__ boundary filter must yield Deploy only once (from deploy.py).
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    _write(tmp_path, "reexport.py", _REEXPORT)
    commands = discover_commands(tmp_path)
    assert _names(commands).count("Deploy") == 1


# --------------------------------------------------------------------------
# resilience: skip ImportError/NotImplementedError, raise on SyntaxError
# --------------------------------------------------------------------------


def test_missing_optional_dep_is_skipped_others_survive(tmp_path):
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    _write(tmp_path, "needy.py", _MISSING_DEP)
    _write(tmp_path, "status.py", _CLASS_CMD_STATUS)
    commands = discover_commands(tmp_path)
    # needy.py raised ImportError -> skipped; the other two survive.
    assert _names(commands) == ["Deploy", "Status"]


def test_missing_optional_dep_skipped_in_package(package_cmds, tmp_path):
    # Add a broken module into the package and confirm the rest still load.
    cmds_dir = tmp_path / "pkg_under_test" / "cmds"
    _write(cmds_dir, "needy.py", _MISSING_DEP)
    commands = discover_commands(package_cmds)
    assert "Needy" not in _names(commands)
    assert set(_names(commands)) >= {"Deploy", "Status", "runme"}


def test_syntax_error_is_not_swallowed(tmp_path):
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    _write(tmp_path, "broken.py", _SYNTAX_ERROR)
    with pytest.raises(SyntaxError):
        discover_commands(tmp_path)


def test_warning_logged_on_skip(tmp_path, caplog):
    _write(tmp_path, "needy.py", _MISSING_DEP)
    with caplog.at_level("WARNING", logger="duho"):
        discover_commands(tmp_path)
    assert any("skipping" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------
# CmdBuilder: dotted import path + file path + provider hook
# --------------------------------------------------------------------------


def test_cmdbuilder_from_dotted_import_path(package_cmds, tmp_path):
    # runme is a module command inside the package.
    builder = CmdBuilder("pkg_under_test.cmds.runme")
    cmd = builder.command
    assert isinstance(cmd, ModuleCommand)
    assert cmd.main() == "module ran"


def test_cmdbuilder_from_file_path(tmp_path):
    path = _write(tmp_path, "runme.py", _MODULE_CMD)
    cmd = CmdBuilder("runme", path).command
    assert isinstance(cmd, ModuleCommand)
    assert cmd.main() == "module ran"


def test_cmdbuilder_unique_sys_modules_name(tmp_path):
    # Importing a loose file whose stem collides with a real module must not
    # clobber the real one in sys.modules.
    path = _write(tmp_path, "json.py", _MODULE_CMD)  # 'json' is a stdlib module
    import json as real_json

    CmdBuilder("json", path)
    assert sys.modules["json"] is real_json  # unchanged


def test_cmdbuilder_bare_directory_without_provider_raises(tmp_path):
    d = tmp_path / "steps"
    d.mkdir()
    _write(d, "01-first.py", _MODULE_CMD)
    with pytest.raises(ImportError):
        CmdBuilder("steps", d)


def test_provider_hook_consulted_for_matching_dir(tmp_path):
    """A registered provider builds a Command for a dir CmdBuilder can't handle."""
    d = tmp_path / "steps"
    d.mkdir()
    _write(d, "01-first.py", _MODULE_CMD)

    sentinel = object()
    calls = {}

    def predicate(path):
        # Match a directory of numbered step files with no __init__.py.
        return path.is_dir() and not (path / "__init__.py").exists()

    def builder(path, qualname):
        calls["path"] = path
        calls["qualname"] = qualname
        return sentinel

    register_command_provider(predicate, builder)
    result = CmdBuilder("steps", d).command
    assert result is sentinel
    assert calls["path"] == d.absolute()
    assert calls["qualname"] == "steps"


def test_provider_not_consulted_for_plain_file(tmp_path):
    """A provider matching only dirs is not consulted for a plain .py file."""
    path = _write(tmp_path, "runme.py", _MODULE_CMD)

    def predicate(p):
        return p.is_dir()

    def builder(p, q):
        raise AssertionError("provider should not run for a plain file")

    register_command_provider(predicate, builder)
    cmd = CmdBuilder("runme", path).command
    assert isinstance(cmd, ModuleCommand)


def test_latest_provider_wins(tmp_path):
    d = tmp_path / "steps"
    d.mkdir()

    def always(path):
        return True

    register_command_provider(always, lambda p, q: "first")
    register_command_provider(always, lambda p, q: "second")
    # Most-recently-registered is consulted first.
    assert CmdBuilder("steps", d).command == "second"


# --------------------------------------------------------------------------
# Integration: discovered commands dispatch through duho.main
# --------------------------------------------------------------------------


def test_discovered_class_command_dispatches(flat_cmds):
    commands = discover_commands(flat_cmds)
    deploy = next(c for c in commands if getattr(c, "__name__", "") == "Deploy")
    # A discovered class command is a normal Cmd: dispatches through duho.main.
    assert duho.main(deploy, ["--env", "staging"], setup_logging=False) == "deployed staging"


def test_discovered_commands_usable_as_subcommands(flat_cmds):
    commands = discover_commands(flat_cmds)
    class_cmds = [c for c in commands if is_class_command(c)]

    class CLI(Cmd):
        """Root."""

        _subcommands_ = class_cmds

        def main(self):
            return None

    # The discovered class commands register as real subcommands and dispatch.
    assert duho.main(CLI, ["Deploy", "--env", "x"], setup_logging=False) == "deployed x"
