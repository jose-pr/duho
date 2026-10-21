"""``_traceback_env_`` names the variable a program uses for tracebacks in place
of ``DUHO_TRACEBACK`` while it runs."""

import pytest

import duho
from duho import Cli, Cmd
from duho.fanout import run_targets
from duho.logging import traceback_enabled
from duho.mcp import _TREE_CACHE, call_tool

SEEN = []


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    SEEN.clear()
    for name in ("DUHO_TRACEBACK", "APP_TRACEBACK"):
        monkeypatch.delenv(name, raising=False)


class Boom(Cmd):
    _parsername_ = "boom"

    def __call__(self):
        SEEN.append(("command", traceback_enabled()))
        raise OSError("disk")


class Stop(Cmd):
    _parsername_ = "stop"

    def __call__(self):
        raise KeyboardInterrupt


class Fan(Cmd):
    _parsername_ = "fan"

    def __call__(self):
        run_targets(lambda t: SEEN.append(("target", traceback_enabled())), [1])


def _hook(message, code):
    SEEN.append(("hook", traceback_enabled()))


class Own(Cli):
    _parsername_ = "own"
    _subcommands_ = [Boom, Stop, Fan]
    _errors_ = {OSError: 74}
    _traceback_env_ = "APP_TRACEBACK"


class Hooked(Own):
    _parsername_ = "own"
    _report_error_ = staticmethod(_hook)


class Plain(Cli):
    _parsername_ = "plain"
    _subcommands_ = [Boom]
    _errors_ = {OSError: 74}


def _entry(kind, root, argv):
    if kind == "main":
        return duho.main(root, argv)
    if kind == "app":
        return duho.app(root, argv=argv)
    return duho.run(root, argv, exit=False)


KINDS = ["main", "app", "run"]


@pytest.mark.parametrize("kind", KINDS)
def test_the_programs_variable_turns_the_re_raise_on(kind, monkeypatch):
    monkeypatch.setenv("APP_TRACEBACK", "1")
    with pytest.raises(OSError):
        _entry(kind, Own, ["boom"])
    assert SEEN == [("command", True)]


@pytest.mark.parametrize("kind", KINDS)
def test_the_duho_variable_alone_does_nothing_to_such_a_program(kind, monkeypatch):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    assert _entry(kind, Own, ["boom"]) == 74
    assert SEEN == [("command", False)]


@pytest.mark.parametrize("kind", KINDS)
def test_a_hook_and_a_command_see_the_programs_switch(kind, monkeypatch):
    monkeypatch.setenv("APP_TRACEBACK", "1")
    assert _entry(kind, Hooked, ["boom"]) == 74
    assert SEEN == [("command", True), ("hook", True)]


def test_ctrl_c_is_re_raised_under_the_programs_variable(monkeypatch):
    monkeypatch.setenv("APP_TRACEBACK", "1")
    with pytest.raises(KeyboardInterrupt):
        duho.run(Own, ["stop"], exit=False)
    monkeypatch.delenv("APP_TRACEBACK")
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    assert duho.run(Own, ["stop"], exit=False) == 130


@pytest.mark.parametrize("kind", KINDS)
def test_outside_a_run_the_duho_variable_is_read_again(kind, monkeypatch):
    monkeypatch.setenv("APP_TRACEBACK", "1")
    with pytest.raises(OSError):
        _entry(kind, Own, ["boom"])
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    monkeypatch.delenv("APP_TRACEBACK")
    assert traceback_enabled() is True
    monkeypatch.delenv("DUHO_TRACEBACK")
    assert traceback_enabled() is False


@pytest.mark.parametrize("kind", KINDS)
def test_a_root_without_the_attribute_is_unchanged(kind, monkeypatch):
    monkeypatch.setenv("APP_TRACEBACK", "1")
    assert _entry(kind, Plain, ["boom"]) == 74
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with pytest.raises(OSError):
        _entry(kind, Plain, ["boom"])


def test_a_fanned_out_target_sees_the_programs_switch(monkeypatch):
    monkeypatch.setenv("APP_TRACEBACK", "1")
    assert duho.main(Own, ["fan"]) == 0
    assert SEEN == [("target", True)]


def test_a_tool_call_hook_sees_the_programs_switch(monkeypatch):
    monkeypatch.setenv("APP_TRACEBACK", "1")
    _TREE_CACHE.pop(Hooked, None)
    try:
        result = call_tool(Hooked, "own.boom", {})
    finally:
        _TREE_CACHE.pop(Hooked, None)
    assert result.get("isError") is True
    assert ("hook", True) in SEEN and ("command", True) in SEEN
