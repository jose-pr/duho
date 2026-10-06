"""Layering onto a module command's declared fields under ``duho.app``:
precedence across env, config file and the command line, and a required
``conflicts`` group satisfied by a layered value."""

import sys

import pytest

import duho
from duho.runtime import app

_PRECEDENCE = '''\
"""Module command with an env- and config-backed field."""
from duho import Arg, NS

SEEN = {}


class Args:
    token: "Arg[str, NS(env='DUHO_TEST_MODLAYER_TOKEN')]" = "default"
    "Auth token"
    ("--token",)


def main(args):
    SEEN["token"] = args.token
    return 0
'''

_GROUP = '''\
"""Module command with a required conflicts group."""
from duho import Arg, NS

SEEN = {}


class Args:
    token: "Arg[str, NS(env='DUHO_TEST_MODLAYER_GTOKEN', conflicts='auth', conflicts_required=True)]" = ""
    ("--token",)
    password: "Arg[str, NS(conflicts='auth')]" = ""
    ("--password",)


def main(args):
    SEEN["token"] = args.token
    SEEN["password"] = args.password
    return 0
'''


class Root(duho.Cmd):
    """root"""

    def __call__(self):  # pragma: no cover - not dispatched
        return 0


def _seen(name):
    return [
        m
        for mod_name, m in sys.modules.items()
        if mod_name.startswith("duho._discovered.") and mod_name.endswith(name)
    ][0].SEEN


def _config(tmp_path, body):
    cfg = tmp_path / "app.toml"
    cfg.write_text(body)
    return str(cfg)


@pytest.mark.requires_toml
def test_env_beats_config(tmp_path, monkeypatch):
    (tmp_path / "modlayer.py").write_text(_PRECEDENCE)
    monkeypatch.setenv("DUHO_TEST_MODLAYER_TOKEN", "from-env")
    cfg = _config(tmp_path, '[modlayer]\ntoken = "from-config"\n')
    rc = app(Root, source=tmp_path, argv=["modlayer"], config=cfg, setup_logging=False)
    assert rc == 0
    assert _seen("modlayer")["token"] == "from-env"


@pytest.mark.requires_toml
def test_command_line_beats_env_and_config(tmp_path, monkeypatch):
    (tmp_path / "modlayer.py").write_text(_PRECEDENCE)
    monkeypatch.setenv("DUHO_TEST_MODLAYER_TOKEN", "from-env")
    cfg = _config(tmp_path, '[modlayer]\ntoken = "from-config"\n')
    rc = app(
        Root,
        source=tmp_path,
        argv=["modlayer", "--token", "from-cli"],
        config=cfg,
        setup_logging=False,
    )
    assert rc == 0
    assert _seen("modlayer")["token"] == "from-cli"


def test_env_satisfies_required_conflicts_group(tmp_path, monkeypatch):
    (tmp_path / "modgroup.py").write_text(_GROUP)
    monkeypatch.setenv("DUHO_TEST_MODLAYER_GTOKEN", "from-env")
    rc = app(Root, source=tmp_path, argv=["modgroup"], setup_logging=False)
    assert rc == 0
    assert _seen("modgroup")["token"] == "from-env"


def test_required_conflicts_group_still_enforced_without_a_value(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "modgroup.py").write_text(_GROUP)
    monkeypatch.delenv("DUHO_TEST_MODLAYER_GTOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        app(Root, source=tmp_path, argv=["modgroup"], setup_logging=False)
    assert exc.value.code == 2
    assert "required" in capsys.readouterr().err
