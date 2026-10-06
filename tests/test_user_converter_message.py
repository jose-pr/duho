"""A user-supplied ``type=`` converter keeps its own error message."""

import argparse
import datetime

import pytest

import duho
from duho import NS, Arg, Meta


def parse_port(text):
    value = int(text)
    if not 1 <= value <= 65535:
        raise ValueError("port must be 1..65535")
    return value


def no_message(text):
    raise ValueError()


def type_error(text):
    raise TypeError("wrong kind of thing")


def _err(cls, argv, capsys):
    with pytest.raises(SystemExit) as exc:
        duho.parse(cls, argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


class WithNS(duho.Cmd):
    port: Arg[int, NS(type=parse_port)] = 80

    def __call__(self):
        return 0


class WithMeta(duho.Cmd):
    port: Arg[int, Meta(type=parse_port)] = 80

    def __call__(self):
        return 0


@pytest.mark.parametrize("cls", [WithNS, WithMeta])
def test_converter_message_is_shown(cls, capsys):
    err = _err(cls, ["--port", "99999"], capsys)
    assert "port must be 1..65535" in err
    assert "invalid" not in err


@pytest.mark.parametrize("cls", [WithNS, WithMeta])
def test_valid_value_still_converts(cls):
    assert duho.parse(cls, ["--port", "8080"]).port == 8080


def test_type_error_message_is_shown(capsys):
    class C(duho.Cmd):
        thing: Arg[str, NS(type=type_error)] = "x"

        def __call__(self):
            return 0

    assert "wrong kind of thing" in _err(C, ["--thing", "a"], capsys)


def test_empty_message_keeps_the_generic_text(capsys):
    class C(duho.Cmd):
        thing: Arg[str, NS(type=no_message)] = "x"

        def __call__(self):
            return 0

    err = _err(C, ["--thing", "a"], capsys)
    assert "invalid no_message value: 'a'" in err


def test_builtin_and_duho_types_keep_the_generic_text(capsys):
    class C(duho.Cmd):
        count: int = 1
        when: datetime.date = datetime.date(2000, 1, 1)
        forced: Arg[int, Meta(type=int)] = 3

        def __call__(self):
            return 0

    assert "invalid int value: 'x'" in _err(C, ["--count", "x"], capsys)
    assert "invalid int value: 'x'" in _err(C, ["--forced", "x"], capsys)
    assert "invalid" in _err(C, ["--when", "nope"], capsys)


def test_converter_that_raises_argument_type_error_is_unchanged(capsys):
    def conv(text):
        raise argparse.ArgumentTypeError("custom explicit text")

    class C(duho.Cmd):
        thing: Arg[str, NS(type=conv)] = "x"

        def __call__(self):
            return 0

    assert "custom explicit text" in _err(C, ["--thing", "a"], capsys)


def test_layered_report_never_echoes_the_converter_message(monkeypatch, capsys):
    class C(duho.Cmd):
        port: Arg[int, NS(type=parse_port, env="DUHO_T_PORT")] = 80

        def __call__(self):
            return 0

    monkeypatch.setenv("DUHO_T_PORT", "99999")
    err = _err(C, [], capsys)
    assert "99999" not in err
    assert "port must be 1..65535" not in err
