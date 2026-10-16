"""A broken pipe ends with status 1 and no output under ``main``, ``app`` and ``run``,
whatever ``DUHO_TRACEBACK`` says and whatever the root's ``_errors_`` maps."""

import gc
import io
import sys

import pytest

import duho
from duho import Cli, Cmd

CALLS = []


class Pipe(Cmd):
    _parsername_ = "pipe"

    def __call__(self):
        raise BrokenPipeError


class Plain(Cmd):
    _parsername_ = "plain"

    def __call__(self):
        raise OSError("disk")


class Interrupt(Cmd):
    _parsername_ = "interrupt"

    def __call__(self):
        raise KeyboardInterrupt


def _hook(message, code):
    CALLS.append((message, code))
    print("custom: %s" % message, file=sys.stderr)


class Root(Cli):
    _parsername_ = "prog"
    _subcommands_ = [Pipe, Plain, Interrupt]
    _errors_ = {OSError: 74}
    _report_error_ = staticmethod(_hook)


@pytest.fixture(autouse=True)
def _clear():
    CALLS.clear()


def _quiet(entry, argv):
    """Run ``entry`` and put back the stdout a silenced run replaced."""
    real = sys.stdout
    try:
        return entry(argv)
    finally:
        if sys.stdout is not real:
            sys.stdout.close()
            sys.stdout = real


def _main(argv):
    return duho.main(Root, argv)


def _app(argv):
    return duho.app(Root, argv=argv)


def _run(argv):
    return duho.run(Root, argv, exit=False)


@pytest.mark.parametrize("traceback", [False, True])
@pytest.mark.parametrize("entry", [_main, _app, _run])
def test_a_broken_pipe_is_status_one_and_silent(entry, traceback, capsys, monkeypatch):
    if traceback:
        monkeypatch.setenv("DUHO_TRACEBACK", "1")
    assert _quiet(entry, ["pipe"]) == 1
    assert capsys.readouterr().err == ""
    assert CALLS == []


@pytest.mark.parametrize("entry", [_main, _app, _run])
def test_a_plain_oserror_still_reaches_the_hook(entry, capsys):
    assert entry(["plain"]) == 74
    assert capsys.readouterr().err == "custom: disk\n"
    assert CALLS == [("disk", 74)]


def test_interrupt_under_the_traceback_variable_still_raises(monkeypatch):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with pytest.raises(KeyboardInterrupt):
        _run(["interrupt"])


def test_a_caller_that_puts_stdout_back_leaves_no_open_file(monkeypatch):
    class Closed(io.StringIO):
        def write(self, text):
            raise BrokenPipeError

    for _ in range(2):
        with monkeypatch.context() as m:
            m.setattr(sys, "stdout", Closed())
            assert duho.main(Root, ["pipe"], setup_logging=False) == 1
    # An unclosed file would warn here, which the suite's settings make an error.
    gc.collect()
