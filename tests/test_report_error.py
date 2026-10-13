"""``_report_error_`` replaces the default error line under ``main``, ``app`` and
``run``; ``run(..., exit=False)`` returns the status instead of exiting."""

import json
import sys

import pytest

import duho
from duho import Cli, Cmd, CommandError
from duho.mcp import call_tool

CALLS = []


class Fails(Cmd):
    _parsername_ = "fails"

    def __call__(self):
        raise CommandError("it broke", 4)


class Quiet(Cmd):
    _parsername_ = "quiet"

    def __call__(self):
        raise CommandError(code=7)


class Mapped(Cmd):
    _parsername_ = "mapped"

    def __call__(self):
        raise FileNotFoundError("nothing there")


class Unmapped(Cmd):
    _parsername_ = "unmapped"

    def __call__(self):
        raise ValueError("not mapped")


def _two(message, code):
    CALLS.append((message, code))


def _three(message, code, error):
    CALLS.append((message, code, error))


def _root(report, name="reporting"):
    return type(
        "Reporting",
        (Cli,),
        {
            "_parsername_": name,
            "_subcommands_": [Fails, Quiet, Mapped, Unmapped],
            "_errors_": {FileNotFoundError: 66},
            "_report_error_": staticmethod(report) if report else None,
        },
    )


@pytest.fixture(autouse=True)
def _clear():
    CALLS.clear()


def _via(kind, root, argv):
    if kind == "main":
        return duho.main(root, argv, setup_logging=False)
    if kind == "app":
        return duho.app(root, argv=argv, setup_logging=False)
    return duho.run(root, argv, exit=False, setup_logging=False)


KINDS = ["main", "app", "run"]


@pytest.mark.parametrize("kind", KINDS)
def test_a_two_argument_hook_replaces_the_default_line(capsys, kind):
    assert _via(kind, _root(_two), ["fails"]) == 4
    assert CALLS == [("it broke", 4)]
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out == ""


@pytest.mark.parametrize("kind", KINDS)
def test_a_three_argument_hook_gets_the_exception(capsys, kind):
    assert _via(kind, _root(_three), ["fails"]) == 4
    ((message, code, error),) = CALLS
    assert (message, code) == ("it broke", 4)
    assert isinstance(error, CommandError) and error.code == 4
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("kind", KINDS)
def test_the_hook_is_called_for_an_empty_message(capsys, kind):
    assert _via(kind, _root(_two), ["quiet"]) == 7
    assert CALLS == [("", 7)]
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("kind", KINDS)
def test_a_mapped_exception_is_reported_through_the_hook(capsys, kind):
    assert _via(kind, _root(_three), ["mapped"]) == 66
    ((message, code, error),) = CALLS
    assert (message, code) == ("nothing there", 66)
    assert isinstance(error, FileNotFoundError)


@pytest.mark.parametrize("kind", KINDS)
def test_without_a_hook_the_default_line_is_printed(capsys, kind):
    assert _via(kind, _root(None), ["fails"]) == 4
    assert capsys.readouterr().err == "reporting: error: it broke\n"
    assert _via(kind, _root(None), ["quiet"]) == 7
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("kind", KINDS)
def test_an_unmapped_exception_is_not_reported_and_propagates(kind):
    with pytest.raises(ValueError, match="not mapped"):
        _via(kind, _root(_two), ["unmapped"])
    assert CALLS == []


@pytest.mark.parametrize("kind", KINDS)
def test_the_traceback_variable_reraises_before_the_hook(monkeypatch, kind):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with pytest.raises(CommandError):
        _via(kind, _root(_two), ["fails"])
    assert CALLS == []


def test_a_lambda_with_defaults_takes_three(capsys):
    seen = []
    root = _root(lambda message, code, error=None: seen.append((message, error)))
    assert duho.main(root, ["fails"], setup_logging=False) == 4
    assert isinstance(seen[0][1], CommandError)


def test_a_tool_call_does_not_use_the_hook():
    root = _root(_two)
    result = call_tool(root, "reporting.fails", {})
    assert result["isError"] is True
    assert "it broke" in result["content"][0]["text"]
    assert CALLS == []


def test_the_attribute_is_a_known_name(caplog):
    root = _root(_two)
    root._parser_()
    assert not [r for r in caplog.records if "_report_error_" in r.getMessage()]


# -- run(exit=False) ---------------------------------------------------------


class Status(Cmd):
    _parsername_ = "status"

    def __call__(self):
        return 3


class Nothing(Cmd):
    _parsername_ = "nothing"

    def __call__(self):
        return None


class Answer(Cmd):
    _parsername_ = "answer"

    def __call__(self):
        return duho.Result(2, value={"rows": 3})


class Interrupt(Cmd):
    _parsername_ = "interrupt"

    def __call__(self):
        raise KeyboardInterrupt


class Pipe(Cmd):
    _parsername_ = "pipe"

    def __call__(self):
        raise BrokenPipeError


class Leave(Cmd):
    _parsername_ = "leave"
    code: str = "0"
    ("--code",)

    def __call__(self):
        raise SystemExit(None if self.code == "none" else int(self.code))


class LeaveText(Cmd):
    _parsername_ = "leave-text"

    def __call__(self):
        raise SystemExit("goodbye")


class Prog(Cli):
    _parsername_ = "prog"
    _subcommands_ = [Status, Nothing, Answer, Interrupt, Pipe, Leave, LeaveText, Fails]


def _returns(argv, **kwargs):
    return duho.run(Prog, argv, exit=False, **kwargs)


def test_exit_false_returns_zero_for_none():
    assert _returns(["nothing"]) == 0


def test_exit_false_returns_the_commands_status():
    assert _returns(["status"]) == 3


def test_exit_false_returns_a_results_status_and_prints_its_value(capsys):
    assert _returns(["answer"]) == 2
    assert capsys.readouterr().out == json.dumps({"rows": 3}, indent=2) + "\n"


def test_exit_false_returns_two_for_a_usage_error(capsys):
    assert _returns(["no-such-command"]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_exit_false_returns_a_command_errors_status(capsys):
    assert _returns(["fails"]) == 4
    assert capsys.readouterr().err == "prog: error: it broke\n"


def test_exit_false_returns_130_for_an_interrupt(capsys):
    assert _returns(["interrupt"]) == 130
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_exit_false_returns_one_for_a_broken_pipe(capsys, monkeypatch):
    real = sys.stdout
    assert _returns(["pipe"]) == 1
    assert sys.stdout is not real
    sys.stdout.close()
    monkeypatch.setattr(sys, "stdout", real)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("code, status", [("none", 0), ("0", 0), ("9", 9)])
def test_exit_false_turns_a_systemexit_into_its_status(code, status):
    assert _returns(["leave", "--code", code]) == status


def test_exit_false_prints_a_non_int_systemexit_and_returns_one(capsys):
    assert _returns(["leave-text"]) == 1
    assert capsys.readouterr().err == "goodbye\n"


def test_exit_false_with_app_keywords(capsys):
    class Extra(Cmd):
        _parsername_ = "extra"

        def __call__(self):
            return "from app"

    assert _returns(["extra"], commands=[Extra]) == 0
    assert capsys.readouterr().out == "from app\n"


def test_exit_false_never_raises_systemexit():
    for argv in (["--help"], ["leave", "--code", "5"], ["bogus"], ["status"]):
        assert isinstance(_returns(argv), int)


def test_the_default_still_exits():
    with pytest.raises(SystemExit) as info:
        duho.run(Prog, ["status"])
    assert info.value.code == 3
    with pytest.raises(SystemExit) as info:
        duho.run(Prog, ["status"], exit=True)
    assert info.value.code == 3


def test_exit_true_lets_a_systemexit_pass_through():
    with pytest.raises(SystemExit) as info:
        duho.run(Prog, ["leave", "--code", "9"])
    assert info.value.code == 9


def test_exit_false_traceback_variable_still_reraises(monkeypatch):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with pytest.raises(KeyboardInterrupt):
        _returns(["interrupt"])
    with pytest.raises(BrokenPipeError):
        _returns(["pipe"])
