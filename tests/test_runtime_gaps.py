"""Runtime `app()` coverage gaps (Plan 03 T4).

Targets the previously-uncovered branches of ``duho.runtime.app`` /
``_resolve_commands``:

* ``env.list("CMDS_PATH")``-driven command resolution (a real tmp dir);
* ``setup_logging=True`` installs a stderr handler once and does not stack;
* a non-``Cmd`` selected leaf -> ``NotImplementedError``;
* ``name=``/``description=`` overrides reach the parser;
* the module ``register`` 3-arg logger fallback when the root has no ``_logger_``;
* a non-dict ``[subcommand]`` config table is tolerated;
* the advisory-prepass ``SystemExit`` path degrades instead of aborting.

Every command source is a REAL ``.py`` file (never ``python -c``), per the
project's AST/-c limitation.
"""

import logging
import os
import sys
from pathlib import Path

import pytest

import duho
from duho import Args, Cmd
from duho.env import Env
from duho.runtime import _resolve_commands, app

_CLASS_CMD = '''\
"""A class command."""
import duho
from duho import Cmd


class Deploy(Cmd):
    """Deploy."""

    name: str = "world"
    "target"
    ("--name",)

    def __call__(self):
        return "deployed " + self.name
'''

_MODULE_REG_3ARG = '''\
"""A module command with a 3-arg register."""
import logging

SEEN = {}


def register(parser, args, logger):
    SEEN["logger_name"] = getattr(logger, "name", None)
    SEEN["is_logger"] = isinstance(logger, logging.Logger)
    parser.add_argument("--flag", default="unset")


def main(args):
    return 0
'''


def _write(dir_path, name, source):
    path = dir_path / name
    path.write_text(source)
    return path


@pytest.fixture(autouse=True)
def _clean_discovered_modules():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name.startswith("duho._discovered."):
            sys.modules.pop(name, None)


class Root(duho.LoggingArgs, Cmd):
    """A LoggingArgs-based root."""

    def __call__(self):  # pragma: no cover - root not dispatched here
        return 0


class PlainRoot(Cmd):
    """A plain Cmd root with no _logger_ (not LoggingArgs)."""

    def __call__(self):  # pragma: no cover
        return 0


# --------------------------------------------------------------------------
# CMDS_PATH-driven resolution
# --------------------------------------------------------------------------


def test_resolve_commands_from_cmds_path_env(tmp_path, monkeypatch):
    """env.paths('CMDS_PATH', ty=Path) with a real dir resolves its commands.

    The single-dir value is an absolute path -- on Windows it carries a
    drive-letter colon (``C:\\...``), which must NOT be split (see
    ``Env.paths`` / ``os.pathsep``).
    """
    monkeypatch.delenv("PATHSEP", raising=False)
    cmd_dir = tmp_path / "cmds"
    cmd_dir.mkdir()
    _write(cmd_dir, "deploy.py", _CLASS_CMD)
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(cmd_dir))
    env = Env("myapp")

    resolved = _resolve_commands(Root, None, None, env, None)
    names = {getattr(c, "__name__", "") for c in resolved}
    assert "Deploy" in names


def test_resolve_commands_from_multi_cmds_path_env(tmp_path, monkeypatch):
    """Two dirs joined by the OS path separator both resolve."""
    import os

    monkeypatch.delenv("PATHSEP", raising=False)
    dir_a = tmp_path / "a"
    dir_a.mkdir()
    _write(dir_a, "deploy.py", _CLASS_CMD)
    dir_b = tmp_path / "b"
    dir_b.mkdir()
    _write(dir_b, "release.py", _CLASS_CMD.replace("Deploy", "Release"))
    monkeypatch.setenv("MYAPP_CMDS_PATH", os.pathsep.join([str(dir_a), str(dir_b)]))
    env = Env("myapp")

    resolved = _resolve_commands(Root, None, None, env, None)
    names = {getattr(c, "__name__", "") for c in resolved}
    assert {"Deploy", "Release"} <= names


def test_app_dispatches_command_from_cmds_path_env(tmp_path, monkeypatch):
    """End-to-end: a CMDS_PATH-resolved command dispatches through app()."""
    cmd_dir = tmp_path / "cmds"
    cmd_dir.mkdir()
    _write(cmd_dir, "deploy.py", _CLASS_CMD)
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(cmd_dir))
    env = Env("myapp")

    rc = app(Root, env=env, argv=["Deploy", "--name", "x"], setup_logging=False)
    assert rc == "deployed x"


# --------------------------------------------------------------------------
# CMDS_PATH security: an empty segment must NEVER import the CWD (D001)
# --------------------------------------------------------------------------

_MODULE_CMD_EVIL = '''\
"""A CWD command that must never be imported."""
from pathlib import Path

Path(r"{marker}").write_text("evil ran")


def main(args=None):
    return "evil ran"
'''


def test_cmds_path_empty_segment_never_imports_cwd(tmp_path, monkeypatch):
    """A leading, trailing, doubled, or separator-only CMDS_PATH value must
    never import (let alone execute) anything from the current directory.

    This is the C11 hazard resurfacing: an empty CMDS_PATH segment used to
    become ``Path("")`` (== ``Path(".")``), which glob-imported and EXECUTED
    every top-level ``.py`` file in the CWD at import time -- the marker file
    below is written as an IMPORT-TIME side effect, not by calling the
    command, so even a bare resolution (no dispatch) must not trigger it.
    `os.pathsep` on this box is ``;`` (Windows); the fix does not hard-code it.
    """
    real_cmds = tmp_path / "cmds"
    real_cmds.mkdir()
    _write(real_cmds, "deploy.py", _CLASS_CMD)

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    marker = cwd / "CANARY_IMPORTED.txt"
    _write(cwd, "evil.py", _MODULE_CMD_EVIL.format(marker=marker))

    monkeypatch.chdir(cwd)
    monkeypatch.delenv("PATHSEP", raising=False)
    sep = os.pathsep

    cases = {
        "leading": f"{sep}{real_cmds}",
        "trailing": f"{real_cmds}{sep}",
        "doubled": f"{real_cmds}{sep}{sep}{real_cmds}",
        "only_sep": sep,
    }
    for label, value in cases.items():
        if marker.exists():
            marker.unlink()
        monkeypatch.setenv("MYAPP_CMDS_PATH", value)
        env = Env("myapp", autoload=False)
        resolved = _resolve_commands(None, None, None, env, None)
        assert not marker.exists(), f"{label}: evil.py was imported from the CWD"
        names = {
            getattr(c, "_parsername_", None) or getattr(c, "__name__", None)
            for c in resolved
        }
        assert "evil" not in names, f"{label}: evil registered as a command"


def test_cmds_path_explicit_dot_segment_is_still_honored(tmp_path, monkeypatch):
    """An explicit '.' segment IS still the current directory (unlike an
    empty one -- see test_cmds_path_empty_segment_never_imports_cwd)."""
    _write(tmp_path, "deploy.py", _CLASS_CMD)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MYAPP_CMDS_PATH", ".")
    env = Env("myapp", autoload=False)

    resolved = _resolve_commands(None, None, None, env, None)
    names = {getattr(c, "__name__", "") for c in resolved}
    assert "Deploy" in names


# --------------------------------------------------------------------------
# CMDS_PATH resilience: one stale entry must not take the app down (D015)
# --------------------------------------------------------------------------


def test_cmds_path_stale_entry_is_skipped_not_fatal(tmp_path, monkeypatch, caplog):
    """A deleted/nonexistent CMDS_PATH directory is skipped with a WARNING;
    the other entries (and built-ins) still resolve."""
    good_dir = tmp_path / "good"
    good_dir.mkdir()
    _write(good_dir, "deploy.py", _CLASS_CMD)
    missing_dir = tmp_path / "does-not-exist"

    monkeypatch.setenv(
        "MYAPP_CMDS_PATH", os.pathsep.join([str(good_dir), str(missing_dir)])
    )
    env = Env("myapp", autoload=False)

    with caplog.at_level("WARNING", logger="duho.runtime"):
        resolved = _resolve_commands(Root, None, None, env, None)

    names = {getattr(c, "__name__", "") for c in resolved}
    assert "Deploy" in names
    assert any("is not a directory" in rec.message for rec in caplog.records)


def test_cmds_path_tilde_is_expanded(tmp_path, monkeypatch):
    """A ``~``-prefixed CMDS_PATH entry is expanded to the home directory."""
    home = tmp_path / "home"
    cmds = home / "cmds"
    cmds.mkdir(parents=True)
    _write(cmds, "deploy.py", _CLASS_CMD)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(Path("~") / "cmds"))
    env = Env("myapp", autoload=False)

    resolved = _resolve_commands(None, None, None, env, None)
    names = {getattr(c, "__name__", "") for c in resolved}
    assert "Deploy" in names


# --------------------------------------------------------------------------
# setup_logging=True
# --------------------------------------------------------------------------


@pytest.fixture
def _clean_root_logger():
    root = logging.getLogger()
    saved = list(root.handlers)
    saved_level = root.level
    yield root
    root.handlers[:] = saved
    root.setLevel(saved_level)


def test_setup_logging_installs_handler_once(tmp_path, _clean_root_logger):
    """setup_logging=True installs a stderr handler; a second app() does not stack.

    A module command leaves the dispatched ``instance`` as the LoggingArgs root,
    so it exposes ``_set_loglevels_`` and the ``setup_logging`` path runs.
    """
    _write(tmp_path, "backup.py", _MODULE_MAIN)
    root = _clean_root_logger
    root.handlers[:] = []  # start from a clean slate

    app(Root, source=tmp_path, argv=["backup"], setup_logging=True)
    count_after_first = len(root.handlers)
    assert count_after_first >= 1

    app(Root, source=tmp_path, argv=["backup"], setup_logging=True)
    # The `if not root_logger.handlers` guard means the second run does not add
    # another handler.
    assert len(root.handlers) == count_after_first


# --------------------------------------------------------------------------
# Non-Cmd selected leaf -> NotImplementedError
# --------------------------------------------------------------------------


class _DataLeaf(Args):
    """A data-only leaf (not a Cmd)."""

    y: int = 2
    ("--y",)


class _CmdParent(Cmd):
    """A Cmd parent whose subcommand leaf is a plain data Args."""

    _parsername_ = "parent"
    _subcommands_ = [_DataLeaf]

    def __call__(self):  # pragma: no cover
        return 0


def test_non_cmd_leaf_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="holds data but is not runnable"):
        app(
            Root,
            commands=[_CmdParent],
            argv=["parent", "_DataLeaf"],
            setup_logging=False,
        )


# --------------------------------------------------------------------------
# name / description overrides reach the parser
# --------------------------------------------------------------------------


def test_name_and_description_overrides_reach_parser(tmp_path, capsys):
    _write(tmp_path, "deploy.py", _CLASS_CMD)
    with pytest.raises(SystemExit):
        app(
            Root,
            source=tmp_path,
            argv=["--help"],
            name="myprog",
            description="My custom description.",
            setup_logging=False,
        )
    out = capsys.readouterr().out
    assert "myprog" in out
    assert "My custom description." in out


# --------------------------------------------------------------------------
# register 3-arg logger fallback (root has no _logger_)
# --------------------------------------------------------------------------


def test_register_3arg_logger_fallback_to_duho(tmp_path):
    """A 3-arg register on a non-LoggingArgs root gets the fallback 'duho' logger."""
    _write(tmp_path, "reg3.py", _MODULE_REG_3ARG)
    rc = app(
        PlainRoot, source=tmp_path, argv=["reg3", "--flag", "v"], setup_logging=False
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("reg3")
    ][0]
    assert discovered.SEEN["is_logger"] is True
    # PlainRoot has no `_logger_`, so app falls back to the module 'duho' logger.
    assert discovered.SEEN["logger_name"] == "duho"


# --------------------------------------------------------------------------
# non-dict [subcommand] config table is tolerated
# --------------------------------------------------------------------------


_MODULE_REG_READS_GLOBAL = '''\
"""A module command whose register reads a root global."""

SEEN = {}


def register(parser, args):
    SEEN["args"] = args
    SEEN["region"] = getattr(args, "region", "<missing>")


def main(args):
    return 0
'''


class _BuiltinHello(Cmd):
    """A builtin subcommand, so the root already has `_subcommands_`."""

    def __call__(self):  # pragma: no cover
        return 0


class RootWithBuiltins(Cmd):
    """A root with a real subparsers action already attached (D016)."""

    region: str = "us"
    "Which region"
    ("--region",)

    _subcommands_ = [_BuiltinHello]

    def __call__(self):  # pragma: no cover
        return 0


def test_register_hook_sees_real_globals_when_root_has_builtin_subcommands(tmp_path):
    """D016: the advisory prepass used to always fail with `KeyError('#cls')`
    when the root already has `_subcommands_` (a real subparsers action
    already exists by prepass time) -- silently swallowed at DEBUG, so every
    module `register` hook got `args=None` instead of the parsed globals."""
    _write(tmp_path, "region_probe.py", _MODULE_REG_READS_GLOBAL)
    rc = app(
        RootWithBuiltins,
        source=tmp_path,
        argv=["--region", "eu", "region-probe"],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.")
        and name.endswith("region_probe")
        and hasattr(m, "SEEN")
    ][0]
    assert discovered.SEEN["args"] is not None
    assert discovered.SEEN["region"] == "eu"


# --------------------------------------------------------------------------
# D017: the advisory prepass must never itself print/exit for real
# --------------------------------------------------------------------------


class VersionedRoot(duho.Cli):
    """A Cli root with --version and --print-completion, and no builtins."""

    _version_ = "9.9.9"
    _completion_ = True

    def __call__(self):  # pragma: no cover
        return 0


def test_version_prints_once_with_a_module_command_present(tmp_path, capsys):
    """`--version` used to print twice through `duho.app` whenever a module
    command triggers the advisory prepass -- the prepass's OWN
    `_VersionAction` printed for real (only `-h`/`--help` was silenced), then
    the real parse printed again."""
    _write(tmp_path, "hello.py", _MODULE_MAIN)
    with pytest.raises(SystemExit) as excinfo:
        app(
            VersionedRoot,
            source=tmp_path,
            argv=["--version"],
            setup_logging=False,
        )
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.count("9.9.9") == 1


def test_print_completion_emits_exactly_one_script(tmp_path, capsys):
    """`--print-completion` used to run for real during the advisory prepass
    too (before any subcommand was registered), then again on the real
    parse -- writing two concatenated scripts, the first incomplete."""
    _write(tmp_path, "hello.py", _MODULE_MAIN)
    with pytest.raises(SystemExit) as excinfo:
        app(
            VersionedRoot,
            source=tmp_path,
            argv=["--print-completion", "bash"],
            setup_logging=False,
        )
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.count("# bash completion for") == 1
    assert "hello" in out  # the ONE script includes the discovered subcommand


def test_non_dict_subcommand_config_table_tolerated(tmp_path):
    """A `[subcommand]` config entry that is a scalar (not a table) is ignored."""
    _write(tmp_path, "deploy.py", _CLASS_CMD)
    config = tmp_path / "app.toml"
    # `Deploy` maps to a scalar, not a table -> the sub-table branch coerces to {}.
    config.write_text('Deploy = "not-a-table"\n')
    rc = app(
        Root,
        source=tmp_path,
        argv=["Deploy", "--name", "z"],
        config=config,
        setup_logging=False,
    )
    assert rc == "deployed z"


# --------------------------------------------------------------------------
# advisory-prepass SystemExit degrades instead of aborting
# --------------------------------------------------------------------------

_MODULE_MAIN = '''\
"""A module command."""


def main(args):
    return 0
'''


class RequiredRoot(duho.LoggingArgs, Cmd):
    """A root with a required global (no default)."""

    token: int
    "A required typed global"
    ("--token",)

    def __call__(self):  # pragma: no cover
        return 0


def test_prepass_systemexit_is_swallowed_real_parse_reports(tmp_path, capsys):
    """A bad required global with a module command present: the advisory prepass
    hits SystemExit (swallowed, silently -- quiet=True), and the real parse
    reports the error exactly once, authoritatively (C5/D017/R038).

    R038: asserting only `pytest.raises(SystemExit)` (as this test used to)
    cannot tell the fixed code from the bug -- an UNSWALLOWED prepass error
    also raises SystemExit here, just with the wrong (or doubled) message.
    Pinning `exc.value.code == 2` and that the error text appears ONCE closes
    that gap.
    """
    _write(tmp_path, "backup.py", _MODULE_MAIN)
    # --token given a non-int: prerun_parse's quiet parse errors (silently)
    # and is swallowed; the authoritative parse below then exits 2, reporting
    # the error exactly once.
    with pytest.raises(SystemExit) as excinfo:
        app(
            RequiredRoot,
            source=tmp_path,
            argv=["--token", "notanint", "backup"],
            setup_logging=False,
        )
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert err.count("invalid int value") == 1


def test_prepass_does_not_preempt_subcommand_help(tmp_path, capsys):
    """R038/D017: `<module-cmd> --help` with a required root global must show
    the SUBCOMMAND's help, not a spurious "arguments are required" error from
    the advisory prepass (which used to run for real, print its own error,
    and only THEN let the real parse show help)."""
    _write(tmp_path, "backup.py", _MODULE_MAIN)
    with pytest.raises(SystemExit) as excinfo:
        app(
            RequiredRoot,
            source=tmp_path,
            argv=["backup", "--help"],
            setup_logging=False,
        )
    assert excinfo.value.code == 0
    out, err = capsys.readouterr()
    assert "arguments are required" not in err
    assert err == ""
    assert "backup" in out
