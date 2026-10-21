"""An exception raised while the config file is chosen or read is reported like a
command's error, and duho's own one-line config error goes through the hook."""

import json

import pytest

import duho
from duho import Cli, CommandError
from duho.exceptions import ConfigError
from duho.mcp import _TREE_CACHE, call_tool

CALLS = []


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    CALLS.clear()
    monkeypatch.delenv("DUHO_TRACEBACK", raising=False)


def _hook(message, code, error):
    CALLS.append((message, code, type(error)))
    print("custom: %s" % message)


def _root(tmp_path, loader=None, text='{"port": 1}', hook=True, errors=True, **extra):
    path = tmp_path / "c.json"
    path.write_text(text)
    attrs = {
        "_parsername_": "prog",
        "_config_": str(path),
        "__call__": lambda self: 0,
        "__annotations__": {},
    }
    if loader is not None:
        attrs["_config_loader_"] = staticmethod(loader)
    if errors:
        attrs["_errors_"] = {KeyError: 66}
    if hook:
        attrs["_report_error_"] = staticmethod(_hook)
    attrs.update(extra)
    return type("Root", (Cli,), attrs)


def _go(kind, root, argv=()):
    argv = list(argv)
    try:
        if kind == "main":
            return duho.main(root, argv)
        if kind == "app":
            return duho.app(root, argv=argv)
        if kind == "parse":
            return duho.run(root, argv, exit="parse")
        return duho.run(root, argv, exit=False)
    except SystemExit as exc:
        return exc.code


KINDS = ["main", "app", "run", "parse"]


def _fail_command(path):
    raise CommandError("no such profile", 7)


def _fail_key(path):
    raise KeyError("profile")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "loader,code,message",
    [(_fail_command, 7, "no such profile"), (_fail_key, 66, "'profile'")],
)
def test_a_loader_error_goes_through_the_hook_with_its_status(
    kind, loader, code, message, tmp_path, capsys
):
    assert _go(kind, _root(tmp_path, loader)) == code
    assert CALLS[0][:2] == (message, code)
    assert capsys.readouterr().out == "custom: %s\n" % message


@pytest.mark.parametrize("kind", KINDS)
def test_a_loader_error_without_a_hook_is_the_default_line(kind, tmp_path, capsys):
    assert _go(kind, _root(tmp_path, _fail_command, hook=False)) == 7
    assert capsys.readouterr().err == "prog: error: no such profile\n"


@pytest.mark.parametrize("kind", KINDS)
def test_an_unmatched_loader_error_propagates(kind, tmp_path):
    def loader(path):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _go(kind, _root(tmp_path, loader))
    assert CALLS == []


@pytest.mark.parametrize("kind", ["main", "app"])
def test_the_traceback_variable_without_a_hook_raises_the_loader_error(
    kind, tmp_path, monkeypatch
):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with pytest.raises(CommandError):
        _go(kind, _root(tmp_path, _fail_command, hook=False))


@pytest.mark.parametrize("kind", KINDS)
def test_help_works_with_a_loader_that_fails(kind, tmp_path, capsys):
    assert _go(kind, _root(tmp_path, _fail_command), ["--help"]) in (0, None)
    assert CALLS == []
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize("kind", KINDS)
def test_choosing_the_file_is_reported_too(kind, tmp_path):
    root = _root(
        tmp_path, _config_field_="missing", _errors_={ValueError: 64}, hook=True
    )
    assert _go(kind, root) == 64
    assert CALLS[0][1] == 64


@pytest.mark.parametrize("kind", KINDS)
def test_a_malformed_file_calls_the_hook_with_a_config_error_and_status_two(
    kind, tmp_path, capsys
):
    # The root maps ValueError, which a ConfigError is, to another status.
    root = _root(tmp_path, text="{", _errors_={ValueError: 64})
    assert _go(kind, root) == 2
    message, code, kind_of_error = CALLS[0]
    assert code == 2 and kind_of_error is ConfigError and "c.json" in message
    captured = capsys.readouterr()
    assert captured.out == "custom: %s\n" % message
    assert captured.err == ""


@pytest.mark.parametrize("kind", KINDS)
def test_a_malformed_file_without_a_hook_prints_the_same_line(kind, tmp_path, capsys):
    assert _go(kind, _root(tmp_path, text="{", hook=False)) == 2
    err = capsys.readouterr().err
    assert err.startswith("prog: error: ") and "c.json" in err
    assert "usage:" not in err


@pytest.mark.parametrize("kind", KINDS)
def test_help_works_with_a_malformed_file_and_a_hook(kind, tmp_path, capsys):
    assert _go(kind, _root(tmp_path, text="{"), ["--help"]) in (0, None)
    assert CALLS == []


def test_a_tool_call_reports_a_malformed_file_through_the_hook(tmp_path):
    root = _root(tmp_path, text="{", _mcp_=True)
    _TREE_CACHE.pop(root, None)
    try:
        result = call_tool(root, "prog", {})
    finally:
        _TREE_CACHE.pop(root, None)
    assert result.get("isError") is True
    assert CALLS and CALLS[0][1:] == (2, ConfigError)
    assert json.dumps(result).count("custom:") == 1
