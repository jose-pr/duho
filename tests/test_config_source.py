"""``duho.config_source``: the config file in force and where its path came from."""

import json

import pytest

import duho
from duho import Cli, Cmd, ConfigArgs, ConfigSource

VAR = "CFGSRC_FILE"
SEEN: list = []


class Leaf(Cmd):
    def __call__(self):
        SEEN.append(duho.config_source(self))
        return 0


def _make(**extra):
    body = {
        "_parsername_": "cfgsrc-tool",
        "_subcommands_": [Leaf],
        "__annotations__": {"port": int},
        "port": 1,
        "__module__": __name__,
        **extra,
    }
    return type("Tool", (ConfigArgs, Cli), body)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    SEEN.clear()


def _file(tmp_path, name="c.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"port": 4}), encoding="utf-8")
    return path


def _run(cls, argv, how="main", **kwargs):
    argv = argv + ["leaf"]
    if how == "main":
        assert duho.main(cls, argv, setup_logging=False, **kwargs) == 0
    else:
        assert duho.app(cls, commands=[], argv=argv, setup_logging=False, **kwargs) == 0
    return SEEN[-1]


HOWS = ["main", "app"]


@pytest.mark.parametrize("how", HOWS)
def test_argument(tmp_path, how):
    path = _file(tmp_path)
    got = _run(_make(), [], how, config=path)
    assert got == ConfigSource(path, "argument")
    assert isinstance(got, tuple) and got.path == path and got.source == "argument"


@pytest.mark.parametrize("how", HOWS)
def test_field(tmp_path, how):
    path = _file(tmp_path)
    got = _run(_make(), ["--config", str(path)], how)
    assert got == ConfigSource(path, "field")


@pytest.mark.parametrize("how", HOWS)
def test_env(tmp_path, monkeypatch, how):
    path = _file(tmp_path)
    monkeypatch.setenv(VAR, str(path))
    got = _run(_make(_config_env_=VAR), [], how)
    assert got == ConfigSource(path, "env")


@pytest.mark.parametrize("how", HOWS)
def test_default(tmp_path, how):
    path = _file(tmp_path)
    got = _run(_make(_config_=str(path)), [], how)
    assert got == ConfigSource(path, "default")


@pytest.mark.parametrize("how", HOWS)
def test_none_without_a_file(how):
    assert _run(_make(), [], how) is None


@pytest.mark.parametrize("how", HOWS)
def test_none_for_an_empty_value(tmp_path, how):
    cls = _make(_config_=str(_file(tmp_path)))
    assert _run(cls, ["--config", ""], how) is None


@pytest.mark.parametrize("how", HOWS)
def test_none_for_an_absent_default_file(tmp_path, how):
    assert _run(_make(_config_=str(tmp_path / "missing.json")), [], how) is None


def test_root_instance(tmp_path):
    path = _file(tmp_path)
    cls = _make(_config_=str(path))
    parsed = duho.parse(cls, ["leaf"])
    assert duho.config_source(parsed) == ConfigSource(path, "default")


def test_directly_built_instance_is_none(tmp_path):
    cls = _make(_config_=str(_file(tmp_path)))
    assert duho.config_source(cls()) is None
    assert duho.config_source(Leaf()) is None
    assert duho.config_source(object()) is None


def test_an_older_instance_keeps_its_own_file(tmp_path):
    first, second = _file(tmp_path, "a.json"), _file(tmp_path, "b.json")
    cls = _make()
    one = duho.parse(cls, ["leaf"], config=first)
    two = duho.parse(cls, ["leaf"], config=second)
    assert duho.config_source(one).path == first
    assert duho.config_source(two).path == second


def test_exported():
    assert "config_source" in duho.__all__ and "ConfigSource" in duho.__all__
    assert isinstance(duho.config_source(Leaf()), type(None))
