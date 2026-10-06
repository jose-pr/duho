"""``duho.testing.invoke``: run a command line in-process and capture the result."""

import os
import sys
import typing

import pytest

import duho
from duho import Arg, Cli, Cmd, NS
from duho.testing import Result, invoke


class _Echo(Cmd):
    """Print the name, log to stderr, and return a status."""

    name: "Arg[str, NS(env='TESTING_INVOKE_NAME')]" = "world"
    ("--name",)

    def __call__(self):
        print("hello", self.name)
        print("to-stderr", file=sys.stderr)
        return 3


class _Reader(Cmd):
    def __call__(self):
        print(sys.stdin.read().upper(), end="")
        return 0


class _Boom(Cmd):
    def __call__(self):
        raise RuntimeError("boom")


class _Quit(Cmd):
    def __call__(self):
        raise SystemExit("goodbye")


def test_result_is_a_named_tuple():
    result = invoke(_Echo, ["--name", "x"])
    assert isinstance(result, tuple) and isinstance(result, Result)
    assert result == (3, "hello x\n", "to-stderr\n")
    assert (result.status, result.stdout, result.stderr) == result


def test_argv_defaults_to_empty():
    assert invoke(_Echo).stdout == "hello world\n"


def test_argparse_error_is_a_status_with_stderr():
    result = invoke(_Echo, ["--nope"])
    assert result.status == 2
    assert "unrecognized arguments" in result.stderr
    assert result.stdout == ""


def test_help_exits_zero_and_captures_stdout():
    result = invoke(_Echo, ["--help"])
    assert result.status == 0
    assert "--name" in result.stdout


def test_system_exit_with_text_is_status_one_and_stderr():
    result = invoke(_Quit)
    assert result.status == 1
    assert "goodbye" in result.stderr


def test_env_is_applied_and_restored(monkeypatch):
    monkeypatch.delenv("TESTING_INVOKE_NAME", raising=False)
    monkeypatch.setenv("TESTING_INVOKE_KEEP", "before")
    result = invoke(
        _Echo, env={"TESTING_INVOKE_NAME": "fromenv", "TESTING_INVOKE_KEEP": "during"}
    )
    assert result.stdout == "hello fromenv\n"
    assert "TESTING_INVOKE_NAME" not in os.environ
    assert os.environ["TESTING_INVOKE_KEEP"] == "before"


def test_stdin_is_fed():
    assert invoke(_Reader, stdin="abc").stdout == "ABC"


def test_streams_are_restored():
    out, err, inp = sys.stdout, sys.stderr, sys.stdin
    invoke(_Echo)
    assert (sys.stdout, sys.stderr, sys.stdin) == (out, err, inp)


def test_other_exceptions_propagate_and_state_is_restored(monkeypatch):
    monkeypatch.delenv("TESTING_INVOKE_X", raising=False)
    out = sys.stdout
    with pytest.raises(RuntimeError, match="boom"):
        invoke(_Boom, env={"TESTING_INVOKE_X": "1"})
    assert "TESTING_INVOKE_X" not in os.environ
    assert sys.stdout is out


def test_app_kwargs_run_app():
    class Root(Cli):
        _parsername_ = "invoke-root"

    result = invoke(Root, ["echo", "--name", "viaapp"], commands=[_Echo])
    assert result == (3, "hello viaapp\n", "to-stderr\n")


def test_annotations_resolve():
    hints = typing.get_type_hints(invoke)
    assert hints["return"] is Result
    assert hints["stdin"] == typing.Optional[str]
    assert hints["env"] == typing.Optional[typing.Mapping[str, str]]
    assert typing.get_type_hints(Result) == {
        "status": int,
        "stdout": str,
        "stderr": str,
    }
