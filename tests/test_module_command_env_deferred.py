"""A bad env value for one module command fails only that command."""

import pytest

from duho.runtime import app

_MOD = '''\
"""Uses a port."""
from duho import Arg, Args, Meta


class Args(Args):
    port: Arg[int, Meta(env="DUHO_TEST_DEFERRED_PORT")] = 80
    "Port"
    ("--port",)


def main(args):
    print("port", args.port)
    return 0
'''

_OTHER = '''\
"""Unrelated."""


def main(args):
    print("other ran")
    return 0
'''


@pytest.fixture
def mods(tmp_path, monkeypatch):
    (tmp_path / "portcmd.py").write_bytes(_MOD.encode())
    (tmp_path / "other.py").write_bytes(_OTHER.encode())
    monkeypatch.setenv("DUHO_TEST_DEFERRED_PORT", "abc")
    return tmp_path


def test_unrelated_command_runs_despite_bad_env(mods, capsys):
    assert app(source=mods, argv=["other"], setup_logging=False) == 0
    assert "other ran" in capsys.readouterr().out


def test_root_help_succeeds_despite_bad_env(mods, capsys):
    with pytest.raises(SystemExit) as exc:
        app(source=mods, argv=["--help"], setup_logging=False)
    assert exc.value.code == 0
    assert "portcmd" in capsys.readouterr().out


def test_owning_command_still_reports_bad_env(mods, capsys):
    with pytest.raises(SystemExit) as exc:
        app(source=mods, argv=["portcmd"], setup_logging=False)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "DUHO_TEST_DEFERRED_PORT" in err
    assert "portcmd" in err


def test_good_env_still_applies(mods, monkeypatch, capsys):
    monkeypatch.setenv("DUHO_TEST_DEFERRED_PORT", "8080")
    assert app(source=mods, argv=["portcmd"], setup_logging=False) == 0
    assert "port 8080" in capsys.readouterr().out
