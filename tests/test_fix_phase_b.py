"""Phase B regression tests: app() drift (C4, C5, M6)."""

import sys

import pytest

import duho
from duho import NS, Arg, Cli, Cmd, LoggingArgs
from duho.runtime import app


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


_CLASS_CMD_DEPLOY = '''\
"""Fixture command file."""

import duho
from duho import Cmd


class Deploy(Cmd):
    """Deploy the thing."""

    name: str = "world"
    "Deploy target name"
    ("--name",)

    def __call__(self):
        return "deployed " + self.name
'''

_MODULE_CMD = '''\
"""A simple module command."""

SEEN = {}


def main(args):
    SEEN["verbose"] = getattr(args, "verbose", None)
    return 0
'''


class _Root(LoggingArgs, Cmd):
    """Root supplying -v/-q globals."""

    def __call__(self):  # pragma: no cover - root not dispatched
        return 0


# -- B1: -v before a class subcommand survives (C4) --------------------------


def test_verbose_before_class_subcommand_survives(tmp_path):
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    captured = {}

    def capture(command, instance):
        captured["verbose"] = getattr(instance, "verbose", None)
        return 0

    app(
        _Root,
        source=tmp_path,
        argv=["-v", "Deploy", "--name", "x"],
        setup_logging=False,
        dispatch=capture,
    )
    assert captured["verbose"] == 1


# -- B1 second scenario: root env survives to the dispatched subcommand (C4) --


class _RootEnv(LoggingArgs, Cli):
    """Root with an env-backed global field."""

    token: Arg[str, NS(env="DUHO_B1_TOKEN")] = "class-default"
    "Auth token"
    ("--token",)

    def __call__(self):  # pragma: no cover - root not dispatched
        return 0


def test_root_env_survives_through_subcommand(tmp_path, monkeypatch):
    monkeypatch.setenv("DUHO_B1_TOKEN", "from-env")
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    captured = {}

    def capture(command, instance):
        captured["token"] = getattr(instance, "token", None)
        return 0

    app(
        _RootEnv,
        source=tmp_path,
        argv=["Deploy", "--name", "x"],
        setup_logging=False,
        dispatch=capture,
    )
    assert captured["token"] == "from-env"


# -- B2: config-supplied required global does not hard-exit the prepass (C5) --


class _CliReq(LoggingArgs, Cli):
    """Root with a required global (no class default)."""

    dsn: str
    "Database DSN"
    ("--dsn",)

    def __call__(self):  # pragma: no cover - root not dispatched
        return 0


def test_config_required_global_with_module_command(tmp_path, monkeypatch):
    monkeypatch.delenv("DUHO_B2_DSN", raising=False)
    cfg = tmp_path / "app.toml"
    cfg.write_text('dsn = "postgres://x"\n')
    _write(tmp_path, "backup.py", _MODULE_CMD)
    # Pre-fix: the advisory prepass parses before config layering and re-raises
    # SystemExit because --dsn is required and not yet supplied.
    rc = app(
        _CliReq,
        source=tmp_path,
        argv=["backup"],
        config=cfg,
        setup_logging=False,
    )
    assert rc == 0


# -- B3: name collision between a module and class command (M6) ---------------


_RAN = {}


class _DeployClass(Cmd):
    """Class command colliding with a module named 'deploy'."""

    _parsername_ = "deploy"

    def __call__(self):
        _RAN["who"] = "class"
        return 0


def test_name_collision_last_wins_and_dispatch_agrees(tmp_path, caplog):
    _RAN.clear()
    _write(tmp_path, "deploy.py", _MODULE_CMD)
    module_cmds = duho.discover_commands(tmp_path)
    # Put the class command LAST so it is the last registered -> should win.
    commands = list(module_cmds) + [_DeployClass]

    with caplog.at_level("WARNING", logger="duho"):
        rc = app(
            _Root,
            commands=commands,
            argv=["deploy"],
            setup_logging=False,
        )
    assert rc == 0
    assert _RAN.get("who") == "class"
    assert any("deploy" in rec.getMessage() for rec in caplog.records)
