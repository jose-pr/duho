"""``duho.config.user_path``: a per-user config location resolved at use."""

import json
import os

import pytest

import duho
from duho import Cli, Cmd
from duho.config import user_path
from duho.config._paths import _config_home, _UserPath

POSIX_HOME = "/home/ann"


def _posix(path, **environ):
    return os.fspath(_UserPath(path, nt=False, environ=environ, home=POSIX_HOME))


def _nt(path, **environ):
    return os.fspath(_UserPath(path, nt=True, environ=environ, home="C:/Users/ann"))


def test_xdg_config_home_is_used_when_absolute(tmp_path):
    base = str(tmp_path)
    assert _posix(("tool", "c.toml"), XDG_CONFIG_HOME=base) == os.path.join(
        base, "tool", "c.toml"
    )


@pytest.mark.parametrize("value", [None, "", "relative/dir"])
def test_xdg_unset_empty_or_relative_falls_back_to_dot_config(value):
    environ = {} if value is None else {"XDG_CONFIG_HOME": value}
    assert _posix(("tool", "c.toml"), **environ) == os.path.join(
        POSIX_HOME, ".config", "tool", "c.toml"
    )


def test_windows_uses_appdata_then_roaming():
    assert _nt(("tool", "c.toml"), APPDATA="D:/Roam") == os.path.join(
        "D:/Roam", "tool", "c.toml"
    )
    for environ in ({}, {"APPDATA": ""}):
        assert _nt(("tool", "c.toml"), **environ) == os.path.join(
            "C:/Users/ann", "AppData", "Roaming", "tool", "c.toml"
        )


def test_windows_ignores_xdg():
    assert _config_home(True, {"XDG_CONFIG_HOME": "/x", "APPDATA": "A"}, "h") == "A"


def test_the_live_platform_branch_is_used(monkeypatch, tmp_path):
    var = "APPDATA" if os.name == "nt" else "XDG_CONFIG_HOME"
    monkeypatch.setenv(var, str(tmp_path))
    assert os.fspath(user_path("tool", "c.toml")) == str(tmp_path / "tool" / "c.toml")


def test_a_later_environment_change_is_seen(monkeypatch, tmp_path):
    var = "APPDATA" if os.name == "nt" else "XDG_CONFIG_HOME"
    path = user_path("tool", "c.toml")
    monkeypatch.setenv(var, str(tmp_path / "one"))
    assert os.fspath(path) == str(tmp_path / "one" / "tool" / "c.toml")
    monkeypatch.setenv(var, str(tmp_path / "two"))
    assert os.fspath(path) == str(tmp_path / "two" / "tool" / "c.toml")


def test_repr_shows_the_call_and_str_the_path(monkeypatch, tmp_path):
    var = "APPDATA" if os.name == "nt" else "XDG_CONFIG_HOME"
    monkeypatch.setenv(var, str(tmp_path))
    path = user_path("tool", "config.toml")
    assert repr(path) == "user_path('tool', 'config.toml')"
    assert str(path) == os.fspath(path)
    assert isinstance(path, os.PathLike)


def test_an_empty_app_is_a_value_error():
    with pytest.raises(ValueError, match="app"):
        user_path("")


def test_no_names_gives_the_app_directory(monkeypatch, tmp_path):
    var = "APPDATA" if os.name == "nt" else "XDG_CONFIG_HOME"
    monkeypatch.setenv(var, str(tmp_path))
    assert os.fspath(user_path("tool")) == str(tmp_path / "tool")


class Leaf(Cmd):
    def __call__(self):
        return 0


class Tool(Cli):
    _parsername_ = "userpath-tool"
    _subcommands_ = [Leaf]
    _config_ = user_path("userpath-tool", "config.json")

    port: int = 1
    ("--port",)


def test_a_root_reads_the_file_the_path_names(monkeypatch, tmp_path):
    var = "APPDATA" if os.name == "nt" else "XDG_CONFIG_HOME"
    monkeypatch.setenv(var, str(tmp_path))
    folder = tmp_path / "userpath-tool"
    folder.mkdir()
    (folder / "config.json").write_text(json.dumps({"port": 6}), encoding="utf-8")
    assert duho.parse(Tool, ["leaf"]).port == 6


def test_a_missing_file_is_skipped_without_error(monkeypatch, tmp_path):
    var = "APPDATA" if os.name == "nt" else "XDG_CONFIG_HOME"
    monkeypatch.setenv(var, str(tmp_path / "nothing-here"))
    assert duho.parse(Tool, ["leaf"]).port == 1
