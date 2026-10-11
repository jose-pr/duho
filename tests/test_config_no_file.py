"""An empty config path means "read no file", wherever a path can be given."""

import json
from pathlib import Path
from typing import Annotated, Optional

import pytest

import duho
from duho import Cli, Cmd, ConfigArgs, Meta
from duho.presets import _config_path

VAR = "NOFILE_CONFIG_FILE"
FIELD_VAR = "NOFILE_CONFIG_FIELD"


class Leaf(Cmd):
    def __call__(self):
        print(f"port={self.port}")
        return 0


def _make(tmp_path, calls, **extra):
    """A root whose default file sets port=7 and which counts loader/validator calls."""
    default = tmp_path / "default.json"
    default.write_text(json.dumps({"port": 7}), encoding="utf-8")

    def loader(path):
        calls.append("loader")
        return json.loads(path.read_text(encoding="utf-8"))

    def validate(args, config, unknown):
        calls.append("validate")

    body = {
        "_parsername_": "nofile-tool",
        "_subcommands_": [Leaf],
        "_config_": str(default),
        "_config_env_": VAR,
        "_config_loader_": staticmethod(loader),
        "_config_validate_": validate,
        "__annotations__": {"port": int},
        "port": 1,
        "__module__": __name__,
        **extra,
    }
    return type("Tool", (ConfigArgs, Cli), body)


def _other(tmp_path, port):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"port": port}), encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    monkeypatch.delenv(FIELD_VAR, raising=False)


def _ports(cls, argv, capsys, **kwargs):
    """The port seen by parse, main and app for one command line."""
    seen = [duho.parse(cls, argv + ["leaf"], **kwargs).port]
    assert duho.main(cls, argv + ["leaf"], setup_logging=False, **kwargs) == 0
    seen.append(int(capsys.readouterr().out.split("port=")[1]))
    assert (
        duho.app(cls, commands=[], argv=argv + ["leaf"], setup_logging=False, **kwargs)
        == 0
    )
    seen.append(int(capsys.readouterr().out.split("port=")[1]))
    return seen


@pytest.mark.parametrize("argv", [["--config", ""], ["--config="], ["-c", ""]])
def test_an_empty_flag_reads_no_file(tmp_path, capsys, argv):
    calls = []
    cls = _make(tmp_path, calls)
    assert _ports(cls, argv, capsys) == [1, 1, 1]
    assert calls == []
    assert duho.parse(cls, argv + ["leaf"]).config is None


def test_an_empty_variable_reads_no_file(tmp_path, capsys, monkeypatch):
    calls = []
    cls = _make(tmp_path, calls)
    monkeypatch.setenv(VAR, "")
    assert _ports(cls, [], capsys) == [1, 1, 1]
    assert calls == []


def test_an_empty_config_argument_reads_no_file(tmp_path, capsys):
    calls = []
    cls = _make(tmp_path, calls)
    assert _ports(cls, [], capsys, config="") == [1, 1, 1]
    assert calls == []


class _EnvTool(ConfigArgs, Cli):
    """``--config`` that also reads its own variable."""

    _parsername_ = "nofile-env"
    _subcommands_ = [Leaf]
    _config_ = None

    config: Annotated[
        Optional[Path],
        Meta(type=_config_path, flags=("--config",), env=FIELD_VAR),
    ] = None
    port: int = 1
    ("--port",)


def test_an_empty_field_variable_reads_no_file(tmp_path, capsys, monkeypatch):
    path = _other(tmp_path, 9)
    monkeypatch.setenv(FIELD_VAR, path)
    assert duho.parse(_EnvTool, ["leaf"]).port == 9
    monkeypatch.setenv(FIELD_VAR, "")
    parsed = duho.parse(_EnvTool, ["leaf"])
    assert (parsed.port, parsed.config) == (1, None)
    assert duho.main(_EnvTool, ["leaf"], setup_logging=False) == 0
    assert "port=1" in capsys.readouterr().out


def test_an_empty_field_variable_beats_the_class_file(tmp_path, monkeypatch):
    cls = type(
        "Tool",
        (_EnvTool,),
        {"_config_": _other(tmp_path, 9), "__module__": __name__},
    )
    assert duho.parse(cls, ["leaf"]).port == 9
    monkeypatch.setenv(FIELD_VAR, "")
    assert duho.parse(cls, ["leaf"]).port == 1


def test_a_non_empty_path_still_wins(tmp_path, capsys, monkeypatch):
    calls = []
    cls = _make(tmp_path, calls)
    other = _other(tmp_path, 9)
    assert _ports(cls, ["--config", other], capsys) == [9, 9, 9]
    monkeypatch.setenv(VAR, other)
    assert _ports(cls, [], capsys) == [9, 9, 9]
    assert _ports(cls, [], capsys, config=other) == [9, 9, 9]


def test_an_unset_variable_falls_back_to_the_class_file(tmp_path, capsys):
    calls = []
    cls = _make(tmp_path, calls)
    assert _ports(cls, [], capsys) == [7, 7, 7]
    assert "loader" in calls


def test_a_named_non_file_is_still_a_usage_error(tmp_path, capsys):
    cls = _make(tmp_path, [])
    with pytest.raises(SystemExit) as info:
        duho.parse(cls, ["--config", "none", "leaf"])
    assert info.value.code == 2
    assert "no such file" in capsys.readouterr().err
