"""CommandError, UsageError and the root's ``_errors_`` map under ``main`` and ``app``."""

import types

import pytest

import duho
from duho import Cli, Cmd, CommandError, UsageError


class Fails(Cmd):
    """Raises CommandError."""

    _parsername_ = "fails"

    def __call__(self):
        raise CommandError("it broke", 4)


class Quiet(Cmd):
    """Raises CommandError with no message."""

    _parsername_ = "quiet"

    def __call__(self):
        raise CommandError(code=7)


class Wrong(Cmd):
    """Raises UsageError."""

    _parsername_ = "wrong"

    def __call__(self):
        raise UsageError("bad combination")


class Plain(Cmd):
    """Raises an exception nobody mapped."""

    _parsername_ = "plain"

    def __call__(self):
        raise ValueError("not mapped")


class Mapped(Cmd):
    """Raises a FileNotFoundError."""

    _parsername_ = "mapped"

    def __call__(self):
        raise FileNotFoundError("nothing there")


class Root(Cli):
    _parsername_ = "boundary"
    _subcommands_ = [Fails, Quiet, Wrong, Plain, Mapped]


class MappedRoot(Root):
    _parsername_ = "boundary"
    _errors_ = {FileNotFoundError: 66, OSError: 74, ValueError: 65}


class OrderRoot(Root):
    _parsername_ = "boundary"
    _errors_ = {OSError: 74, FileNotFoundError: 66}


def test_command_error_prints_message_and_returns_code(capsys):
    assert duho.main(Root, ["fails"]) == 4
    captured = capsys.readouterr()
    assert captured.err == "boundary: error: it broke\n"
    assert captured.out == ""


def test_empty_message_prints_nothing_and_returns_code(capsys):
    assert duho.main(Root, ["quiet"]) == 7
    assert capsys.readouterr().err == ""


def test_usage_error_defaults_to_two(capsys):
    assert duho.main(Root, ["wrong"]) == 2
    assert capsys.readouterr().err == "boundary: error: bad combination\n"


def test_exception_attributes():
    exc = CommandError("m", 9)
    assert (exc.message, exc.code, str(exc)) == ("m", 9, "m")
    assert str(CommandError()) == ""
    assert CommandError().code == 1
    assert isinstance(UsageError("x"), CommandError)
    assert UsageError().code == 2


def test_traceback_env_reraises(monkeypatch):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with pytest.raises(CommandError, match="it broke"):
        duho.main(Root, ["fails"])


def test_app_catches_command_error(capsys):
    assert duho.app(Root, argv=["fails"]) == 4
    assert capsys.readouterr().err == "boundary: error: it broke\n"


def test_app_traceback_env_reraises(monkeypatch):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with pytest.raises(UsageError):
        duho.app(Root, argv=["wrong"])


def test_app_custom_dispatch_is_wrapped(capsys):
    def dispatch(command, instance):
        raise CommandError("from dispatch", 3)

    assert duho.app(Root, argv=["fails"], dispatch=dispatch) == 3
    assert capsys.readouterr().err == "boundary: error: from dispatch\n"


def test_app_module_command_path_is_wrapped(capsys):
    module = types.ModuleType("modfail")
    module.__doc__ = "Fails."

    def main(args):
        raise CommandError("m", 6)

    main.__module__ = "modfail"
    module.main = main
    cmd = duho.ModuleCommand(module)
    assert duho.app(Root, commands=[cmd], argv=["modfail"]) == 6
    assert capsys.readouterr().err == "boundary: error: m\n"


def test_errors_map_subclass_match_and_message(capsys):
    assert duho.main(MappedRoot, ["mapped"]) == 66
    assert capsys.readouterr().err == "boundary: error: nothing there\n"
    assert duho.main(MappedRoot, ["plain"]) == 65


def test_errors_map_first_match_in_mapping_order():
    assert duho.main(MappedRoot, ["mapped"]) == 66
    assert duho.main(OrderRoot, ["mapped"]) == 74


def test_errors_map_under_app(capsys):
    assert duho.app(MappedRoot, argv=["plain"]) == 65
    assert capsys.readouterr().err == "boundary: error: not mapped\n"


def test_errors_map_traceback_env_reraises(monkeypatch):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with pytest.raises(ValueError):
        duho.main(MappedRoot, ["plain"])


def test_default_catches_nothing():
    assert Root._errors_ is None
    with pytest.raises(ValueError, match="not mapped"):
        duho.main(Root, ["plain"])
    with pytest.raises(ValueError, match="not mapped"):
        duho.app(Root, argv=["plain"])


def test_unmapped_exception_still_propagates_with_a_map():
    class Other(Cmd):
        _parsername_ = "other"

        def __call__(self):
            raise KeyError("k")

    class R(MappedRoot):
        _subcommands_ = [Other]

    with pytest.raises(KeyError):
        duho.main(R, ["other"])


def test_systemexit_from_a_command_is_not_caught():
    class Exits(Cmd):
        _parsername_ = "exits"

        def __call__(self):
            raise SystemExit(5)

    class R(Cli):
        _parsername_ = "r"
        _subcommands_ = [Exits]

    with pytest.raises(SystemExit) as info:
        duho.main(R, ["exits"])
    assert info.value.code == 5
