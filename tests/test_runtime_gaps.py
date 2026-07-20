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
import sys

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
    monkeypatch.setenv(
        "MYAPP_CMDS_PATH", os.pathsep.join([str(dir_a), str(dir_b)])
    )
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
        app(Root, commands=[_CmdParent], argv=["parent", "_DataLeaf"], setup_logging=False)


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


def test_prepass_systemexit_is_swallowed_real_parse_reports(tmp_path):
    """A bad required global with a module command present: the advisory prepass
    hits SystemExit (swallowed), and the real parse reports the error (C5)."""
    _write(tmp_path, "backup.py", _MODULE_MAIN)
    # --token given a non-int: prerun_parse's parse errors (SystemExit) and is
    # swallowed; the authoritative parse below then exits 2.
    with pytest.raises(SystemExit):
        app(
            RequiredRoot,
            source=tmp_path,
            argv=["--token", "notanint", "backup"],
            setup_logging=False,
        )
