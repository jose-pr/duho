"""MCP results for ``CommandError``, the ``_errors_`` map and ``duho.Result`` returns."""

import sys

from duho import Cli, Cmd, CommandError, Result, UsageError
from duho.mcp import call_tool


class RaisesError(Cmd):
    _parsername_ = "raises-error"

    def __call__(self):
        print("partial out")
        print("partial err", file=sys.stderr)
        raise CommandError("no value for KEY")


class RaisesUsage(Cmd):
    _parsername_ = "raises-usage"

    def __call__(self):
        raise UsageError("bad combination")


class RaisesMapped(Cmd):
    _parsername_ = "raises-mapped"

    def __call__(self):
        raise FileNotFoundError("gone")


class RaisesPlain(Cmd):
    _parsername_ = "raises-plain"

    def __call__(self):
        raise RuntimeError("kaboom")


class Words(Cmd):
    _parsername_ = "words"

    def __call__(self):
        print("printed")
        return Result(1, text="no value found", is_error=False)


class Value(Cmd):
    _parsername_ = "value"

    def __call__(self):
        return Result(0, value={"rows": 3})


class TextBeatsValue(Cmd):
    _parsername_ = "text-beats-value"

    def __call__(self):
        return Result(0, value={"rows": 3}, text="three rows")


class BareFailure(Cmd):
    _parsername_ = "bare-failure"

    def __call__(self):
        print("out")
        return Result(4)


class SilentOk(Cmd):
    _parsername_ = "silent-ok"

    def __call__(self):
        return Result(0)


class Plain4(Cmd):
    _parsername_ = "plain4"

    def __call__(self):
        return 4


class Plain1(Cmd):
    _parsername_ = "plain1"

    def __call__(self):
        return 1


_TREE = [
    RaisesError,
    RaisesUsage,
    RaisesMapped,
    RaisesPlain,
    Words,
    Value,
    TextBeatsValue,
    BareFailure,
    SilentOk,
    Plain4,
    Plain1,
]


class Declared(Cli):
    _parsername_ = "declared"
    _subcommands_ = _TREE
    _errors_ = {FileNotFoundError: 66}
    _exit_codes_ = {4: "no match", 1: "lookup failed"}


class Undeclared(Cli):
    _parsername_ = "undeclared"
    _subcommands_ = _TREE


def _call(root, name):
    return call_tool(root, root._parsername_ + "." + name, {})


def _text(result):
    return result["content"][0]["text"]


def test_command_error_is_error_text_with_streams_and_no_type_prefix():
    result = _call(Undeclared, "raises-error")
    assert result["isError"] is True
    assert _text(result) == "partial out\n\npartial err\nno value for KEY"


def test_usage_error_is_error_text():
    result = _call(Undeclared, "raises-usage")
    assert result["isError"] is True
    assert _text(result) == "bad combination"


def test_mapped_exception_is_error_text_without_type():
    result = _call(Declared, "raises-mapped")
    assert result["isError"] is True
    assert _text(result) == "gone"


def test_unmapped_exception_keeps_the_type_prefix():
    for root in (Declared, Undeclared):
        result = _call(root, "raises-plain")
        assert result["isError"] is True
        assert _text(result) == "RuntimeError: kaboom"
    assert _text(_call(Undeclared, "raises-mapped")) == "FileNotFoundError: gone"


def test_command_error_is_not_logged_as_broken(caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        _call(Undeclared, "raises-error")
    assert not [r for r in caplog.records if "raised" in r.getMessage()]


def test_result_text_and_is_error_flag_follow_the_result():
    result = _call(Undeclared, "words")
    assert "isError" not in result
    assert _text(result) == "printed\n\nno value found"


def test_result_value_is_the_rendered_text():
    result = _call(Undeclared, "value")
    assert "isError" not in result
    assert '"rows": 3' in _text(result)


def test_result_text_wins_over_value():
    assert _text(_call(Undeclared, "text-beats-value")) == "three rows"


def test_bare_failing_result_has_todays_exit_line_and_is_error():
    result = _call(Undeclared, "bare-failure")
    assert result["isError"] is True
    assert _text(result) == "out\n\nexit code: 4"


def test_zero_result_without_words_is_a_quiet_success():
    result = _call(Undeclared, "silent-ok")
    assert result == {"content": [{"type": "text", "text": ""}]}


def test_declared_exit_code_gets_its_meaning():
    assert _text(_call(Declared, "plain4")) == "exit code: 4 (no match)"
    assert _text(_call(Declared, "plain1")) == "exit code: 1 (lookup failed)"
    assert _text(_call(Declared, "bare-failure")) == "out\n\nexit code: 4 (no match)"


def test_root_without_exit_codes_gives_todays_exact_text():
    result = _call(Undeclared, "plain4")
    assert result == {
        "content": [{"type": "text", "text": "exit code: 4"}],
        "isError": True,
    }
    assert _text(_call(Undeclared, "plain1")) == "exit code: 1"


def test_builtin_exit_code_defaults_are_not_used():
    class OnlyFour(Cli):
        _parsername_ = "onlyfour"
        _subcommands_ = [Plain1]
        _exit_codes_ = {4: "something"}

    assert _text(_call(OnlyFour, "plain1")) == "exit code: 1"
