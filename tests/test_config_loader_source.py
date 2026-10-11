"""A ``_config_loader_`` taking two arguments is told where its path came from."""

import json

import pytest

import duho
from duho import Cli, Cmd, ConfigArgs
from duho.mcp import _TREE_CACHE, call_tool

VAR = "LOADSRC_CONFIG_FILE"


class Leaf(Cmd):
    def __call__(self):
        return 0


def _write(tmp_path, name="c.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"port": 4}), encoding="utf-8")
    return str(path)


def _make(seen, loader=None, **extra):
    def two(path, source):
        seen.append((path.name, source))
        return json.loads(path.read_text(encoding="utf-8"))

    body = {
        "_parsername_": "loadsrc-tool",
        "_subcommands_": [Leaf],
        "_config_env_": VAR,
        "_config_loader_": staticmethod(loader or two),
        "__annotations__": {"port": int},
        "port": 1,
        "__module__": __name__,
        **extra,
    }
    return type("Tool", (ConfigArgs, Cli), body)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(VAR, raising=False)


def _run(cls, argv, how, **kwargs):
    if how == "main":
        assert duho.main(cls, argv + ["leaf"], setup_logging=False, **kwargs) == 0
    else:
        assert (
            duho.app(
                cls, commands=[], argv=argv + ["leaf"], setup_logging=False, **kwargs
            )
            == 0
        )


@pytest.mark.parametrize("how", ["main", "app"])
def test_each_source_is_reported(tmp_path, monkeypatch, how):
    seen = []
    path = _write(tmp_path, "a.json")
    cls = _make(seen, _config_=_write(tmp_path, "d.json"))

    _run(cls, [], how, config=path)
    _run(cls, ["--config", path], how)
    monkeypatch.setenv(VAR, path)
    _run(cls, [], how)
    monkeypatch.delenv(VAR)
    _run(cls, [], how)
    assert seen == [
        ("a.json", "argument"),
        ("a.json", "field"),
        ("a.json", "env"),
        ("d.json", "default"),
    ]


def test_parse_reports_the_source_too(tmp_path):
    seen = []
    cls = _make(seen, _config_=_write(tmp_path, "d.json"))
    path = _write(tmp_path, "a.json")
    duho.parse(cls, ["leaf"], config=path)
    duho.parse(cls, ["--config", path, "leaf"])
    duho.parse(cls, ["leaf"])
    assert [s for _, s in seen] == ["argument", "field", "default"]


def test_a_tree_served_over_mcp_reports_default(tmp_path):
    seen = []

    class Served(Cmd):
        _parsername_ = "loadsrc-served"
        _config_ = _write(tmp_path, "d.json")
        _config_loader_ = staticmethod(
            lambda path, source: (seen.append(source), {"port": 4})[1]
        )
        port: int = 1
        ("--port",)

        def __call__(self):
            return {"port": self.port}

    _TREE_CACHE.pop(Served, None)
    try:
        result = call_tool(Served, "loadsrc-served", {})
    finally:
        _TREE_CACHE.pop(Served, None)
    assert result.get("isError") is not True
    assert seen and set(seen) == {"default"}


def test_a_one_argument_loader_is_called_as_before(tmp_path):
    seen = []

    def one(path):
        seen.append(path.name)
        return {"port": 4}

    cls = _make(seen, one)
    assert duho.parse(cls, ["--config", _write(tmp_path), "leaf"]).port == 4
    assert seen == ["c.json"]


def test_a_staticmethod_and_a_one_argument_lambda_are_called_as_before(tmp_path):
    seen = []
    cls = _make(seen, lambda path: seen.append(path.name) or {"port": 5})
    assert duho.parse(cls, ["--config", _write(tmp_path), "leaf"]).port == 5
    assert seen == ["c.json"]


def test_a_variadic_loader_gets_the_source(tmp_path):
    seen = []
    cls = _make(seen, lambda *args: seen.append(args[1]) or {})
    duho.parse(cls, ["--config", _write(tmp_path), "leaf"])
    assert seen == ["field"]
