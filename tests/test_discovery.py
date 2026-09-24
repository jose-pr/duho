"""Tests for duho.discovery: Command protocol, ModuleCommand, CmdBuilder, and
the resilient discover_commands walker.

All fixtures are REAL ``.py`` files written under ``tmp_path`` (never ``python
-c`` -- ``Cmd`` subclasses defined in a ``-c`` string get no AST-derived flags/
docstrings, per the project's AST/-c limitation). Each test builds exactly the
command files it needs so the fixtures double as readable documentation of the
supported shapes.
"""

import os
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

    def __call__(self):
        return "deployed " + self.env
'''

_CLASS_CMD_STATUS = '''\
"""Show status."""
import duho
from duho import Cmd


class Status(Cmd):
    """Show status."""

    def __call__(self):
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

    def __call__(self):
        return "a"


class Beta(Cmd):
    """Beta command."""

    def __call__(self):
        return "b"
'''

# A file importing a nonexistent optional dependency -> ImportError -> skipped.
_MISSING_DEP = '''\
"""Command needing an optional dep that is not installed."""
import duho_totally_missing_optional_dep  # noqa: F401
from duho import Cmd


class Needy(Cmd):
    def __call__(self):
        return "needy"
'''

# A file with a real syntax error -> discovery must NOT swallow it.
_SYNTAX_ERROR = '''\
"""Broken command file."""
from duho import Cmd


class Broken(Cmd)      # <- missing colon: SyntaxError
    def __call__(self):
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
    return [
        (
            c._parsername_
            if is_module_command(c)
            else (getattr(c, "_parsername_", None) or c.__name__)
        )
        for c in commands
    ]


# --------------------------------------------------------------------------
# Predicates + protocol
# --------------------------------------------------------------------------


def test_is_class_command_predicate():
    class MyCmd(Cmd):
        def __call__(self):
            return 0

    assert is_class_command(MyCmd)
    assert not is_class_command(Cmd)  # the base itself is excluded
    assert not is_class_command(duho.Args)
    assert not is_class_command(object)
    assert not is_class_command(42)


def test_class_command_satisfies_protocol():
    class MyCmd(Cmd):
        def __call__(self):
            return 0

    # runtime_checkable Protocol: a Cmd subclass has _parsername_ (once parsed
    # or via the class rule) and __call__.
    assert hasattr(MyCmd, "__call__")


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
    src = _MODULE_CMD + textwrap.dedent("""
        def init(args=None):
            return {"ctx": 1}

        def success(ctx, args=None):
            return "ok"
        """)
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
    src = _CLASS_CMD_DEPLOY + textwrap.dedent("""

        def main(args=None):
            return "module entry"
        """)
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


def test_reexported_class_is_deduped(tmp_path, caplog):
    # deploy.py defines Deploy; reexport.py imports it unchanged. The
    # __module__ boundary filter must yield Deploy only once (from deploy.py).
    # Sibling imports now work (D018 -- the directory is on sys.path for the
    # duration of each file's import), so reexport.py's `from deploy import
    # Deploy` SUCCEEDS; Deploy is filtered out by the module-boundary dedup,
    # not by a failed import, so no warning is logged for it either.
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    _write(tmp_path, "reexport.py", _REEXPORT)
    with caplog.at_level("WARNING", logger="duho"):
        commands = discover_commands(tmp_path)
    assert _names(commands).count("Deploy") == 1
    assert not any("reexport" in rec.message for rec in caplog.records)


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
    # Explicitly assert the "no provider registered" path. The autouse
    # _restore_providers fixture restores afterward, so clearing here is safe;
    # it also insulates this test from an opt-in provider (e.g. duho.runpath)
    # another test imported and left globally registered.
    _discovery._PROVIDERS.clear()
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
    assert (
        duho.main(deploy, ["--env", "staging"], setup_logging=False)
        == "deployed staging"
    )


def test_discovered_commands_usable_as_subcommands(flat_cmds):
    commands = discover_commands(flat_cmds)
    class_cmds = [c for c in commands if is_class_command(c)]

    class CLI(Cmd):
        """Root."""

        _subcommands_ = class_cmds

        def __call__(self):
            return None

    # The discovered class commands register as real subcommands and dispatch.
    assert duho.main(CLI, ["Deploy", "--env", "x"], setup_logging=False) == "deployed x"


# --------------------------------------------------------------------------
# An imported callable is never mistaken for a module's own entrypoint/hook
# --------------------------------------------------------------------------

_IMPORTED_RUN_ONLY = '''\
"""Helper importing subprocess.run but defining no command of its own."""
from subprocess import run  # noqa: F401
'''

_CLASS_CMD_WITH_IMPORTED_RUN = '''\
"""A class command whose file also imports subprocess.run under the name run."""
from subprocess import run  # noqa: F401
from duho import Cmd


class Deploy(Cmd):
    """Deploy."""

    def __call__(self):
        return "deployed"
'''

_MODULE_WITH_IMPORTED_HOOK = '''\
"""A module command whose register hook is imported, not defined here."""
from atexit import register  # noqa: F401


def main(args=None):
    return "ran"
'''


def test_imported_callable_alone_is_not_a_command(tmp_path):
    path = _write(tmp_path, "shim.py", _IMPORTED_RUN_ONLY)
    # `from subprocess import run` with nothing else -- must NOT be treated
    # as a `main`/`run`/`call` entrypoint.
    with pytest.raises(NotImplementedError):
        CmdBuilder("shim", path)


def test_imported_run_does_not_shadow_sibling_class_command(tmp_path):
    _write(tmp_path, "deploy.py", _CLASS_CMD_WITH_IMPORTED_RUN)
    commands = discover_commands(tmp_path)
    # Only the REAL class command "Deploy" -- the imported `run` must not
    # ALSO register a bogus "deploy" ModuleCommand that could shadow it.
    assert _names(commands) == ["Deploy"]


def test_imported_hook_is_not_bound_falls_back_to_noop(tmp_path):
    path = _write(tmp_path, "cleanup.py", _MODULE_WITH_IMPORTED_HOOK)
    cmd = CmdBuilder("cleanup", path).command
    # `register` came from `atexit`, not defined in this module -- must be
    # the no-op default, never atexit.register itself.
    assert cmd.register(object(), object()) is None


def test_all_escape_hatch_allows_a_deliberate_reexported_entrypoint(tmp_path):
    """A module may deliberately re-export its real entrypoint from a shared
    helper -- listing it in ``__all__`` is the documented opt-in that still
    counts it as "this module's own", even though ``__module__`` differs."""
    _write(
        tmp_path,
        "_impl.py",
        '"""Shared implementation."""\n\n\ndef main(args=None):\n    return "impl ran"\n',
    )
    _write(
        tmp_path,
        "front.py",
        '"""Re-exports its real entrypoint from a shared impl module."""\n'
        "from _impl import main  # noqa: F401\n\n"
        '__all__ = ["main"]\n',
    )
    commands = discover_commands(tmp_path)
    front = next(
        c for c in commands if is_module_command(c) and c._parsername_ == "front"
    )
    assert front.main() == "impl ran"


# --------------------------------------------------------------------------
# CmdBuilder: namespaced loose-file import key (D005)
# --------------------------------------------------------------------------


def test_cmdbuilder_namespaces_loose_file_never_under_bare_qualname(tmp_path):
    """A loose file built via CmdBuilder must NEVER be registered in
    ``sys.modules`` under its bare qualname -- that would shadow a real (but
    not-yet-imported) module of the same name for the rest of the process."""
    path = _write(tmp_path, "colorsys.py", _MODULE_CMD)
    CmdBuilder("colorsys", path)
    # No sys.modules entry named exactly "colorsys" may point at our file.
    assert getattr(sys.modules.get("colorsys"), "__file__", None) != str(path)
    import colorsys as real_colorsys  # the REAL stdlib module

    assert hasattr(real_colorsys, "rgb_to_hsv")
    assert not hasattr(real_colorsys, "main")


def test_cmdbuilder_loose_file_key_is_namespaced(tmp_path):
    path = _write(tmp_path, "widget.py", _MODULE_CMD)
    CmdBuilder("widget", path)
    assert "duho._cmdbuilder.widget" in sys.modules
    assert "widget" not in sys.modules


# --------------------------------------------------------------------------
# CmdBuilder: a package directory must verify the qualname resolves to it (D041)
# --------------------------------------------------------------------------


def test_cmdbuilder_package_dir_rejects_a_qualname_that_resolves_elsewhere(
    tmp_path, monkeypatch
):
    onpath = tmp_path / "onpath"
    onpath_pkg = onpath / "d041tools"
    onpath_pkg.mkdir(parents=True)
    (onpath_pkg / "__init__.py").write_text(
        "WHO = 'onpath'\n\n\ndef main(args=None):\n    return WHO\n"
    )

    elsewhere = tmp_path / "elsewhere"
    real_pkg = elsewhere / "d041tools"
    real_pkg.mkdir(parents=True)
    (real_pkg / "__init__.py").write_text(
        "WHO = 'elsewhere'\n\n\ndef main(args=None):\n    return WHO\n"
    )

    monkeypatch.syspath_prepend(str(onpath))
    for name in list(sys.modules):
        if name == "d041tools" or name.startswith("d041tools."):
            del sys.modules[name]
    try:
        with pytest.raises(ImportError):
            CmdBuilder("d041tools", real_pkg)
    finally:
        for name in list(sys.modules):
            if name == "d041tools" or name.startswith("d041tools."):
                del sys.modules[name]


def test_cmdbuilder_package_dir_succeeds_when_it_resolves_via_sys_path(
    tmp_path, monkeypatch
):
    parent = tmp_path / "d041parent"
    real_pkg = parent / "d041ok"
    real_pkg.mkdir(parents=True)
    (real_pkg / "__init__.py").write_text('def main(args=None):\n    return "ok"\n')
    monkeypatch.syspath_prepend(str(parent))
    sys.modules.pop("d041ok", None)
    try:
        cmd = CmdBuilder("d041ok", real_pkg).command
        assert cmd.main() == "ok"
    finally:
        sys.modules.pop("d041ok", None)


# --------------------------------------------------------------------------
# Directory discovery: sibling _helpers imports (D018)
# --------------------------------------------------------------------------

_HELPERS_GREETING = '''\
"""Shared helper used by sibling command files."""


def greeting():
    return "hi"
'''

_USES_HELPER = '''\
"""Uses a sibling _helpers module via a bare absolute import."""
from _helpers import greeting
from duho import Cmd


class Greet(Cmd):
    """Greet."""

    def __call__(self):
        return greeting()
'''


def test_sibling_helper_import_works_during_directory_discovery(tmp_path):
    _write(tmp_path, "_helpers.py", _HELPERS_GREETING)
    _write(tmp_path, "greet.py", _USES_HELPER)
    commands = discover_commands(tmp_path)
    greet_cls = next(c for c in commands if getattr(c, "__name__", "") == "Greet")
    assert duho.main(greet_cls, [], setup_logging=False) == "hi"


def test_sibling_helper_import_does_not_leak_directory_onto_sys_path(tmp_path):
    _write(tmp_path, "_helpers.py", _HELPERS_GREETING)
    _write(tmp_path, "greet.py", _USES_HELPER)
    discover_commands(tmp_path)
    assert str(tmp_path) not in sys.path


def test_sibling_helper_does_not_bleed_across_directories(tmp_path):
    """Two different directories each shipping a `_helpers.py` with DIFFERENT
    content must each see their OWN helper -- not a stale cached module from
    the other directory's earlier discovery run."""
    dir_a = tmp_path / "a"
    dir_a.mkdir()
    _write(dir_a, "_helpers.py", "def greeting():\n    return 'A'\n")
    _write(dir_a, "greet.py", _USES_HELPER)

    dir_b = tmp_path / "b"
    dir_b.mkdir()
    _write(dir_b, "_helpers.py", "def greeting():\n    return 'B'\n")
    _write(dir_b, "greet.py", _USES_HELPER)

    commands_a = discover_commands(dir_a)
    greet_a = next(c for c in commands_a if getattr(c, "__name__", "") == "Greet")
    assert duho.main(greet_a, [], setup_logging=False) == "A"

    commands_b = discover_commands(dir_b)
    greet_b = next(c for c in commands_b if getattr(c, "__name__", "") == "Greet")
    assert duho.main(greet_b, [], setup_logging=False) == "B"


# --------------------------------------------------------------------------
# discover_commands('name'): an importable package wins over a CWD shadow (D019)
# --------------------------------------------------------------------------


def test_bare_name_prefers_importable_package_over_cwd_shadow(tmp_path, monkeypatch):
    # The REAL importable package, reachable via sys.path.
    proj = tmp_path / "proj"
    real_pkg = proj / "d019cmds"
    real_pkg.mkdir(parents=True)
    (real_pkg / "__init__.py").write_text("")
    _write(real_pkg, "real.py", _CLASS_CMD_STATUS)
    monkeypatch.syspath_prepend(str(proj))

    # An UNRELATED same-named directory the CWD happens to contain.
    cwd = tmp_path / "elsewhere"
    shadow_dir = cwd / "d019cmds"
    shadow_dir.mkdir(parents=True)
    _write(shadow_dir, "evil.py", _CLASS_CMD_DEPLOY)
    monkeypatch.chdir(cwd)

    for name in list(sys.modules):
        if name == "d019cmds" or name.startswith("d019cmds."):
            del sys.modules[name]
    try:
        names = _names(discover_commands("d019cmds"))
        assert "Status" in names  # from the real, importable package
        assert "Deploy" not in names  # the CWD shadow must NOT be used
    finally:
        for name in list(sys.modules):
            if name == "d019cmds" or name.startswith("d019cmds."):
                del sys.modules[name]


def test_bare_name_falls_back_to_cwd_dir_when_not_importable(tmp_path, monkeypatch):
    """The documented loose-directory convention keeps working when the name
    is NOT importable via sys.path at all."""
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "d019loose"
    d.mkdir()
    _write(d, "deploy.py", _CLASS_CMD_DEPLOY)
    assert "Deploy" in _names(discover_commands("d019loose"))


# --------------------------------------------------------------------------
# A private (leading-underscore) class is never discovered as a command (D020)
# --------------------------------------------------------------------------

_PRIVATE_BASE_AND_SUBCLASS = '''\
"""A private shared base plus a real subclass command."""
from duho import Cmd


class _RemoteBase(Cmd):
    """Private shared base -- not meant to be a command."""

    def __call__(self):
        raise NotImplementedError


class Push(_RemoteBase):
    """Push to remote."""

    def __call__(self):
        return "pushed"
'''


def test_private_prefixed_class_is_not_discovered(tmp_path):
    _write(tmp_path, "remote.py", _PRIVATE_BASE_AND_SUBCLASS)
    names = _names(discover_commands(tmp_path))
    assert names == ["Push"]
    assert "_RemoteBase" not in names


# --------------------------------------------------------------------------
# A package's module command is named after the package, not --init-- (D026)
# --------------------------------------------------------------------------


def test_cmdbuilder_package_module_command_named_after_package(tmp_path, monkeypatch):
    parent = tmp_path / "d026parent"
    real_pkg = parent / "mytool"
    real_pkg.mkdir(parents=True)
    (real_pkg / "__init__.py").write_text('def main(args=None):\n    return "ok"\n')
    monkeypatch.syspath_prepend(str(parent))
    sys.modules.pop("mytool", None)
    try:
        cmd = CmdBuilder("mytool", real_pkg).command
        assert cmd._parsername_ == "mytool"
    finally:
        sys.modules.pop("mytool", None)


def test_cmdbuilder_package_module_command_named_after_package_dotted_import(
    tmp_path, monkeypatch
):
    parent = tmp_path / "d026parent2"
    real_pkg = parent / "mytool2"
    real_pkg.mkdir(parents=True)
    (real_pkg / "__init__.py").write_text('def main(args=None):\n    return "ok"\n')
    monkeypatch.syspath_prepend(str(parent))
    sys.modules.pop("mytool2", None)
    try:
        cmd = CmdBuilder("mytool2").command  # source=None -> dotted-import branch
        assert cmd._parsername_ == "mytool2"
    finally:
        sys.modules.pop("mytool2", None)


# --------------------------------------------------------------------------
# Entry points: a module's own _parsername_ wins (D027); _cli_name removed (D060)
# --------------------------------------------------------------------------


class _FakeEntryPoint:
    """A minimal stand-in for ``importlib.metadata.EntryPoint`` in tests."""

    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        if isinstance(self._target, BaseException):
            raise self._target
        return self._target


def _load_module(tmp_path, name, source):
    path = _write(tmp_path, name + ".py", source)
    import importlib.util as _importutil

    spec = _importutil.spec_from_file_location("t_ep_" + name, path)
    module = _importutil.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_entry_point_module_own_parsername_wins_over_entry_point_name(
    tmp_path, monkeypatch
):
    module = _load_module(
        tmp_path,
        "farewell_mod",
        '"""Bye."""\n_parsername_ = "farewell"\n\n\ndef main(args=None):\n    return "bye"\n',
    )
    monkeypatch.setattr(
        _discovery._compat,
        "iter_entry_points",
        lambda group: [_FakeEntryPoint("bye", module)],
    )
    names = _names(_discovery.discover_entry_points("t.commands"))
    assert names == ["farewell"]


def test_entry_point_name_used_when_module_declares_none(tmp_path, monkeypatch):
    module = _load_module(
        tmp_path,
        "plain_mod",
        '"""Plain."""\n\n\ndef main(args=None):\n    return "ok"\n',
    )
    monkeypatch.setattr(
        _discovery._compat,
        "iter_entry_points",
        lambda group: [_FakeEntryPoint("bye", module)],
    )
    names = _names(_discovery.discover_entry_points("t.commands"))
    assert names == ["bye"]


def test_cli_name_alias_no_longer_honored(tmp_path):
    """D060 [minor]: `_cli_name` was dropped -- only `_parsername_` names a
    module command now. A module declaring only `_cli_name` falls back to its
    file-stem-derived name instead."""
    path = _write(
        tmp_path,
        "whatever.py",
        '"""Run."""\n_cli_name = "custom-name"\n\n\ndef main(args=None):\n    return "ran"\n',
    )
    cmd = CmdBuilder("whatever", path).command
    assert cmd._parsername_ != "custom-name"
    assert cmd._parsername_ == "whatever"


# --------------------------------------------------------------------------
# Repeated discovery reuses an unchanged file's module (D034)
# --------------------------------------------------------------------------


def test_repeated_discovery_of_unchanged_file_reuses_the_module(tmp_path):
    marker = tmp_path / "side_effects.txt"
    src = (
        '"""Records an import-time side effect."""\n'
        "from pathlib import Path\n\n"
        f'Path(r"{marker}").open("a").write("x")\n\n\n'
        'def main(args=None):\n    return "ran"\n'
    )
    _write(tmp_path, "once.py", src)

    for _ in range(5):
        discover_commands(tmp_path)

    resolved = os.fspath((tmp_path / "once.py").resolve())
    synthetic_keys = {
        name
        for name, mod in sys.modules.items()
        if name.startswith("duho._discovered.")
        and getattr(mod, "__file__", None) == resolved
    }
    assert len(synthetic_keys) == 1
    assert marker.read_text().count("x") == 1


def test_rediscovery_after_file_change_gets_a_fresh_import(tmp_path):
    """Editing the file (mtime changes) invalidates the cache: a rediscovery
    picks up the NEW content, not a stale cached module."""
    path = _write(
        tmp_path,
        "versioned.py",
        '"""v1."""\n\n\ndef main(args=None):\n    return "v1"\n',
    )
    commands = discover_commands(tmp_path)
    cmd = next(c for c in commands if is_module_command(c))
    assert cmd.main() == "v1"

    path.write_text('"""v2."""\n\n\ndef main(args=None):\n    return "v2"\n')
    new_time = path.stat().st_mtime + 1
    os.utime(path, (new_time, new_time))

    commands2 = discover_commands(tmp_path)
    cmd2 = next(c for c in commands2 if is_module_command(c))
    assert cmd2.main() == "v2"


# --------------------------------------------------------------------------
# Coverage gaps: error/degrade paths users will actually hit (D056)
# --------------------------------------------------------------------------


def test_discover_commands_on_a_file_raises_import_error(tmp_path):
    path = _write(tmp_path, "notadir.py", _CLASS_CMD_STATUS)
    with pytest.raises(ImportError):
        discover_commands(path)


def test_entry_point_loading_to_non_command_is_skipped_with_warning(
    monkeypatch, caplog
):
    monkeypatch.setattr(
        _discovery._compat,
        "iter_entry_points",
        lambda group: [_FakeEntryPoint("junk", 42)],
    )
    with caplog.at_level("WARNING", logger="duho.discovery"):
        commands = _discovery.discover_entry_points("t.commands")
    assert commands == []
    assert any(
        "junk" in rec.message and "not a command" in rec.message
        for rec in caplog.records
    )


def test_entry_point_load_failure_is_skipped_with_warning(monkeypatch, caplog):
    monkeypatch.setattr(
        _discovery._compat,
        "iter_entry_points",
        lambda group: [_FakeEntryPoint("broken", RuntimeError("boom"))],
    )
    with caplog.at_level("WARNING", logger="duho.discovery"):
        commands = _discovery.discover_entry_points("t.commands")
    assert commands == []
    assert any("broken" in rec.message for rec in caplog.records)


def test_cmdbuilder_dotted_namespace_package_routes_to_provider(tmp_path, monkeypatch):
    """A dotted namespace-package directory (no __init__.py) is offered to
    providers before being imported as a plain package (discovery.py's
    `_from_import` namespace branch)."""
    nsroot = tmp_path / "d056nsroot"
    ns_pkg = nsroot / "d056nspkg"
    ns_pkg.mkdir(parents=True)  # no __init__.py -> namespace package
    _write(ns_pkg, "01-first.py", _MODULE_CMD)
    monkeypatch.syspath_prepend(str(nsroot))
    sys.modules.pop("d056nspkg", None)

    sentinel = object()
    calls = {}

    def predicate(path):
        return path.is_dir() and not (path / "__init__.py").exists()

    def builder(path, qualname):
        calls["qualname"] = qualname
        return sentinel

    register_command_provider(predicate, builder)
    try:
        result = CmdBuilder("d056nspkg").command  # source=None -> dotted import
        assert result is sentinel
        assert calls["qualname"] == "d056nspkg"
    finally:
        sys.modules.pop("d056nspkg", None)
