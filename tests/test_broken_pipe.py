"""A broken pipe ends with status 1 and no output under ``main``, ``app`` and ``run``,
whatever ``DUHO_TRACEBACK`` says and whatever the root's ``_errors_`` maps."""

import errno
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


class _Dead(io.StringIO):
    """A stdout whose reader left: the write and the flush fail with ``code``."""

    def __init__(self, code):
        super().__init__()
        self.code = code

    def write(self, text):
        raise OSError(self.code, "closed")

    def flush(self):
        raise OSError(self.code, "closed")


class _FlushOnly(io.StringIO):
    """A stdout that takes every write and fails when it is flushed."""

    def __init__(self, code):
        super().__init__()
        self.code = code

    def flush(self):
        raise OSError(self.code, "closed")


class Writes(Cmd):
    _parsername_ = "writes"

    def __call__(self):
        sys.stdout.write("x")


class Quiet(Cmd):
    _parsername_ = "quiet"

    def __call__(self):
        return None


class Invalid(Cmd):
    _parsername_ = "invalid"

    def __call__(self):
        raise OSError(errno.EINVAL, "not stdout")


class Named(Cmd):
    _parsername_ = "named"

    def __call__(self):
        raise OSError(errno.EINVAL, "bad", "some-file")


class Edge(Cli):
    _parsername_ = "edge"
    _subcommands_ = [Writes, Quiet, Invalid, Named]
    _errors_ = {OSError: 74}
    _report_error_ = staticmethod(_hook)


class Bare(Cli):
    _parsername_ = "bare"
    _subcommands_ = [Writes, Quiet]


def _entry(kind, root):
    if kind == "main":
        return lambda argv: duho.main(root, argv)
    if kind == "app":
        return lambda argv: duho.app(root, argv=argv)
    return lambda argv: duho.run(root, argv, exit=False)


@pytest.mark.parametrize("root", [Edge, Bare])
@pytest.mark.parametrize("traceback", [False, True])
@pytest.mark.parametrize("kind", ["main", "app", "run"])
@pytest.mark.parametrize("code", [errno.EINVAL, errno.EPIPE])
def test_a_write_to_a_closed_stdout_is_status_one_and_silent(
    code, kind, traceback, root, capsys, monkeypatch
):
    if traceback:
        monkeypatch.setenv("DUHO_TRACEBACK", "1")
    monkeypatch.setattr(sys, "stdout", _Dead(code))
    assert _entry(kind, root)(["writes"]) == 1
    monkeypatch.undo()
    assert capsys.readouterr().err == ""
    assert CALLS == []


@pytest.mark.parametrize("kind", ["main", "app", "run"])
def test_an_einval_that_is_not_stdout_still_reaches_the_hook(kind, capsys):
    assert _entry(kind, Edge)(["invalid"]) == 74
    assert capsys.readouterr().err == "custom: [Errno 22] not stdout\n"
    assert CALLS == [("[Errno 22] not stdout", 74)]


@pytest.mark.parametrize("kind", ["main", "app"])
def test_an_error_naming_a_file_is_not_a_closed_stdout(kind, monkeypatch):
    monkeypatch.setattr(sys, "stdout", _Dead(errno.EINVAL))
    assert _entry(kind, Edge)(["named"]) == 74
    assert [code for _, code in CALLS] == [74]


@pytest.mark.parametrize("code", [errno.EINVAL, errno.EPIPE])
def test_a_stdout_that_fails_only_on_the_final_flush_is_status_one(
    code, capsys, monkeypatch
):
    seen = []
    monkeypatch.setattr(sys, "stdout", _FlushOnly(code))
    status = duho.run(
        Bare, ["quiet"], exit=lambda status, error: seen.append(error) or False
    )
    monkeypatch.undo()
    assert status == 1
    assert isinstance(seen[0], BrokenPipeError)
    assert capsys.readouterr().err == ""
    gc.collect()


def test_a_flush_that_fails_for_another_reason_propagates(monkeypatch):
    monkeypatch.setattr(sys, "stdout", _FlushOnly(errno.EIO))
    with pytest.raises(OSError):
        duho.run(Bare, ["quiet"], exit=False)
