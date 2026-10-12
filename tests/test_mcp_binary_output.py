"""A tool call captures standard output as bytes: text comes back inline,
bytes that are not text go to the output descriptor."""

import os
import sys

import pytest

from duho import Cli, Cmd
from duho.mcp import call_tool

BLOB = b"\xff\xfe\x00binary\x80"
BINARY_FLAG = getattr(os, "O_BINARY", 0)


class Texty(Cmd):
    """Prints lines, one with a carriage return."""

    def __call__(self):
        print("one")
        print("two\r")
        sys.stdout.write("three\n\n")
        return 0


class Surrogate(Cmd):
    """Prints a lone surrogate."""

    def __call__(self):
        print("a\udc80b")
        return 0


class WriteBytes(Cmd):
    """Writes bytes that are not text."""

    def __call__(self):
        sys.stdout.buffer.write(BLOB)
        return 0


class TextThenBytes(Cmd):
    """Text, then bytes, then text, in one call."""

    def __call__(self):
        print("head", end="")
        sys.stdout.buffer.write(b"\xff")
        print("tail", end="")
        return 0


class Utf8Bytes(Cmd):
    """Writes valid UTF-8 through the buffer."""

    def __call__(self):
        sys.stdout.buffer.write("café\n".encode("utf-8"))
        return 0


class BytesThenFail(Cmd):
    """Writes bytes and exits non-zero."""

    def __call__(self):
        sys.stdout.buffer.write(BLOB)
        return 4


class BytesThenExit(Cmd):
    """Writes bytes and raises SystemExit."""

    def __call__(self):
        sys.stdout.buffer.write(BLOB)
        raise SystemExit(3)


class BytesThenValue(Cmd):
    """Writes bytes and returns a value."""

    def __call__(self):
        sys.stdout.buffer.write(BLOB)
        return {"ok": True}


class Root(Cli):
    """Root."""

    _parsername_ = "bin"
    _subcommands_ = [
        Texty,
        Surrogate,
        WriteBytes,
        TextThenBytes,
        Utf8Bytes,
        BytesThenFail,
        BytesThenExit,
        BytesThenValue,
    ]


def _text(result):
    assert len(result["content"]) == 1
    return result["content"][0]["text"]


def _call(name, **kwargs):
    return call_tool(Root, "bin." + name, {}, **kwargs)


class _Pipe:
    """A pipe whose ends are closed on exit, the read end drained first."""

    def __enter__(self):
        self.read_fd, self.write_fd = os.pipe()
        return self

    def __exit__(self, *exc):
        for fd in (self.read_fd, self.write_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def test_text_output_is_returned_exactly():
    result = _call("texty")
    assert "isError" not in result
    assert _text(result) == "one\ntwo\r\nthree\n\n"


def test_a_lone_surrogate_stays_printable():
    assert _text(_call("surrogate")) == "a\\udc80b\n"


def test_utf8_bytes_written_through_the_buffer_are_text():
    assert _text(_call("utf8-bytes")) == "café\n"


def test_bytes_with_no_descriptor_are_an_error_and_written_nowhere(capfd):
    result = _call("write-bytes")
    assert result["isError"] is True
    assert _text(result) == (
        "the command wrote 10 bytes of output that is not text, and this server "
        "has no output descriptor: start it with file descriptor 3 open, or "
        "name an open one in <NAME>_MCP_OUTPUT"
    )
    assert capfd.readouterr() == ("", "")


def test_the_error_names_the_variable_it_was_given():
    result = _call("write-bytes", output_var="BIN_MCP_OUTPUT")
    assert _text(result).endswith("name an open one in BIN_MCP_OUTPUT")


def test_bytes_go_to_the_descriptor_unchanged():
    with _Pipe() as pipe:
        result = _call("write-bytes", output_fd=pipe.write_fd)
        assert os.read(pipe.read_fd, 100) == BLOB
        fd = pipe.write_fd
    assert "isError" not in result
    assert _text(result) == (
        "10 bytes of output that is not text were written to file descriptor "
        "%d, at offset 0" % fd
    )


def test_two_calls_report_consecutive_offsets_on_a_file(tmp_path):
    path = tmp_path / "out.bin"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | BINARY_FLAG)
    try:
        first = _text(_call("write-bytes", output_fd=fd))
        second = _text(_call("write-bytes", output_fd=fd))
    finally:
        os.close(fd)
    assert first.endswith(", at offset 0")
    assert second.endswith(", at offset 10")
    assert path.read_bytes() == BLOB + BLOB


def test_the_file_path_is_named_only_where_the_platform_can_tell(tmp_path):
    path = tmp_path / "named.bin"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT)
    try:
        text = _text(_call("write-bytes", output_fd=fd))
    finally:
        os.close(fd)
    if sys.platform.startswith("linux"):
        assert "(%s)" % os.path.realpath(str(path)) in text
    else:
        assert "(" not in text


def test_text_and_bytes_in_one_call_go_whole_to_the_descriptor(tmp_path):
    path = tmp_path / "mixed.bin"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | BINARY_FLAG)
    try:
        result = _call("text-then-bytes", output_fd=fd)
    finally:
        os.close(fd)
    assert path.read_bytes() == b"head\xfftail"
    assert _text(result).startswith("9 bytes of output that is not text were written")


def test_a_pipe_counts_the_bytes_this_process_wrote():
    with _Pipe() as pipe:
        first = _text(_call("write-bytes", output_fd=pipe.write_fd))
        second = _text(_call("write-bytes", output_fd=pipe.write_fd))
    assert first.endswith(", at offset 0")
    assert second.endswith(", at offset 10")


def test_a_closed_descriptor_is_an_error_result():
    with _Pipe() as pipe:
        fd = pipe.write_fd
        os.close(pipe.write_fd)
        result = _call("write-bytes", output_fd=fd)
    assert result["isError"] is True
    message = _text(result)
    assert message.startswith(
        "could not write the command's output that is not text to file "
        "descriptor %d: " % fd
    )
    assert "binary" not in message


def test_bytes_replace_the_text_in_a_failing_result():
    with _Pipe() as pipe:
        result = _call("bytes-then-fail", output_fd=pipe.write_fd)
    assert result["isError"] is True
    lines = _text(result).split("\n")
    assert lines[0].startswith("10 bytes of output that is not text were written")
    assert lines[1:] == ["exit code: 4"]


def test_bytes_before_system_exit_are_sent_too():
    with _Pipe() as pipe:
        result = _call("bytes-then-exit", output_fd=pipe.write_fd)
        assert os.read(pipe.read_fd, 100) == BLOB
    assert result["isError"] is True
    assert _text(result).split("\n")[-1] == "exit code: 3"


def test_bytes_before_a_returned_value_are_sent_and_the_value_follows():
    with _Pipe() as pipe:
        result = _call("bytes-then-value", output_fd=pipe.write_fd)
    first, rest = _text(result).split("\n", 1)
    assert first.startswith("10 bytes of output that is not text were written")
    assert '"ok": true' in rest


def test_a_command_that_only_prints_never_touches_the_descriptor():
    with _Pipe() as pipe:
        result = _call("texty", output_fd=pipe.write_fd)
        os.close(pipe.write_fd)
        assert os.read(pipe.read_fd, 100) == b""
    assert _text(result) == "one\ntwo\r\nthree\n\n"


@pytest.fixture(autouse=True)
def _fresh_write_counts():
    from duho.mcp import _call as call_module

    call_module._WRITTEN.clear()
    yield
    call_module._WRITTEN.clear()
