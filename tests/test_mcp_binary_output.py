"""A tool call captures standard output as bytes: text comes back inline,
bytes that are not text go to the output descriptor."""

import io
import json
import os
import subprocess
import sys
import textwrap

import pytest

from conftest import subprocess_env
from duho import Cli, Cmd
from duho.mcp import call_tool, serve
from duho.mcp._call import _OutputStream, _OutputTarget
from duho.mcp._stdio import _resolve_output_target

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


PROBE = {"path": None, "seen": None}


class StreamsBeforeReturn(Cmd):
    """Writes bytes, then reads the target through a second handle."""

    def __call__(self):
        sys.stdout.buffer.write(b"\xff\xfe")
        with open(PROBE["path"], "rb") as handle:
            PROBE["seen"] = handle.read()
        return 0


class UsesFileno(Cmd):
    """Held text, then a write straight to the descriptor stdout names."""

    def __call__(self):
        print("held", end="")
        os.write(sys.stdout.fileno(), b"-direct")
        return 0


class Filenos(Cmd):
    """Reports what fileno() and isatty() do."""

    def __call__(self):
        try:
            sys.stdout.fileno()
        except io.UnsupportedOperation:
            print("unsupported", sys.stdout.isatty())
        return 0


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
        StreamsBeforeReturn,
        UsesFileno,
        Filenos,
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
        "%d, at offset 0 of this server's output" % fd
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
    assert first.endswith(", at offset 0 of this server's output")
    assert second.endswith(", at offset 10 of this server's output")


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


# --------------------------------------------------------------------------
# The stream under sys.stdout
# --------------------------------------------------------------------------


def test_text_split_across_writes_is_not_an_error_without_a_target():
    stream = _OutputStream(None)
    encoded = "café".encode("utf-8")
    stream.write(encoded[:4])
    stream.write(encoded[4:])
    assert stream.finish(None).text == "café"


def test_a_truncated_character_at_the_end_is_not_text():
    stream = _OutputStream(None)
    stream.write("café".encode("utf-8")[:-1])
    assert stream.finish("V").error is not None


def test_without_a_target_the_stream_stops_holding_after_invalid_bytes():
    stream = _OutputStream(None)
    stream.write(b"text")
    stream.write(b"\xff")
    assert bytes(stream._held) == b""
    stream.write(b"x" * 100000)
    assert bytes(stream._held) == b""
    assert stream._count == 4 + 1 + 100000
    assert "100005 bytes" in stream.finish("V").error["content"][0]["text"]


def test_held_text_then_invalid_bytes_arrive_whole_and_in_order(tmp_path):
    fd = os.open(str(tmp_path / "o.bin"), os.O_WRONLY | os.O_CREAT | BINARY_FLAG)
    try:
        stream = _OutputStream(_OutputTarget(fd))
        stream.write(b"ab")
        stream.write(b"c")
        stream.write(b"\xff")
        stream.write(b"d")
        done = stream.finish(None)
    finally:
        os.close(fd)
    assert (tmp_path / "o.bin").read_bytes() == b"abc\xffd"
    assert done.wrote and done.text.startswith("5 bytes of output")


def test_fileno_and_isatty_without_a_target():
    stream = _OutputStream(None)
    with pytest.raises(io.UnsupportedOperation):
        stream.fileno()
    assert stream.isatty() is False
    assert _text(_call("filenos")) == "unsupported False\n"


def test_fileno_with_a_target_sends_what_is_held_first(tmp_path):
    path = tmp_path / "f.bin"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | BINARY_FLAG)
    try:
        result = _call("uses-fileno", output_fd=fd, output_path=str(path))
    finally:
        os.close(fd)
    assert path.read_bytes() == b"held-direct"
    assert _text(result) == (
        "11 bytes of output that is not text were written to file descriptor "
        "%d (%s), at offset 0" % (fd, path)
    )


def test_bytes_reach_the_target_before_the_command_returns(tmp_path):
    path = tmp_path / "early.bin"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | BINARY_FLAG)
    PROBE["path"] = str(path)
    try:
        _call("streams-before-return", output_fd=fd, output_path=str(path))
    finally:
        os.close(fd)
    assert PROBE["seen"] == b"\xff\xfe"


# --------------------------------------------------------------------------
# The output target: resolved once when a server starts
# --------------------------------------------------------------------------

NOT_POSIX = pytest.mark.skipif(os.name != "posix", reason="needs fd inheritance")


def _inheritable_fd(tmp_path, name="fd.bin"):
    fd = os.open(str(tmp_path / name), os.O_WRONLY | os.O_CREAT | BINARY_FLAG)
    os.set_inheritable(fd, True)
    return fd


def _closed_fd():
    r, w = os.pipe()
    os.close(r)
    os.close(w)
    return w


def test_an_open_inheritable_default_is_the_output_descriptor(tmp_path):
    fd = _inheritable_fd(tmp_path)
    try:
        target = _resolve_output_target({}, "BIN_MCP_OUTPUT", default=fd)
        assert target == (fd, None)
    finally:
        os.close(fd)


def test_a_closed_default_is_no_output_descriptor():
    assert _resolve_output_target({}, "BIN_MCP_OUTPUT", default=_closed_fd()) is None


def test_a_descriptor_the_program_opened_itself_is_never_taken(tmp_path):
    fd = os.open(str(tmp_path / "own.bin"), os.O_WRONLY | os.O_CREAT)
    try:
        assert not os.get_inheritable(fd)
        assert _resolve_output_target({}, "BIN_MCP_OUTPUT", default=fd) is None
    finally:
        os.close(fd)


def test_the_variable_names_the_descriptor_in_place_of_the_default(tmp_path):
    fd = _inheritable_fd(tmp_path)
    try:
        environ = {"BIN_MCP_OUTPUT": " %d " % fd}
        target = _resolve_output_target(environ, "BIN_MCP_OUTPUT", default=_closed_fd())
        assert target == (fd, None)
    finally:
        os.close(fd)


def test_an_empty_variable_is_unset():
    environ = {"BIN_MCP_OUTPUT": "  "}
    default = _closed_fd()
    assert _resolve_output_target(environ, "BIN_MCP_OUTPUT", default=default) is None


def test_a_variable_naming_a_closed_descriptor_raises():
    environ = {"BIN_MCP_OUTPUT": str(_closed_fd())}
    with pytest.raises(ValueError, match="BIN_MCP_OUTPUT"):
        _resolve_output_target(environ, "BIN_MCP_OUTPUT")


def test_a_variable_naming_a_non_inheritable_descriptor_raises(tmp_path):
    fd = os.open(str(tmp_path / "own.bin"), os.O_WRONLY | os.O_CREAT)
    try:
        with pytest.raises(ValueError, match="BIN_MCP_OUTPUT"):
            _resolve_output_target({"BIN_MCP_OUTPUT": str(fd)}, "BIN_MCP_OUTPUT")
    finally:
        os.close(fd)


def test_a_variable_holding_a_file_name_is_opened_for_appending(tmp_path):
    path = tmp_path / "named.bin"
    path.write_bytes(b"old")
    target = _resolve_output_target({"BIN_MCP_OUTPUT": str(path)}, "BIN_MCP_OUTPUT")
    try:
        os.write(target.fd, b"new")
    finally:
        os.close(target.fd)
    assert target.path == str(path)
    assert path.read_bytes() == b"oldnew"


def test_a_file_name_has_its_home_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    target = _resolve_output_target({"BIN_MCP_OUTPUT": "~/home.bin"}, "BIN_MCP_OUTPUT")
    os.close(target.fd)
    assert os.path.samefile(target.path, str(tmp_path / "home.bin"))


def test_a_file_that_cannot_be_opened_raises_naming_the_variable(tmp_path):
    missing = tmp_path / "no" / "such" / "dir.bin"
    with pytest.raises(ValueError, match="BIN_MCP_OUTPUT") as caught:
        _resolve_output_target({"BIN_MCP_OUTPUT": str(missing)}, "BIN_MCP_OUTPUT")
    assert str(missing) not in str(caught.value)


def _requests(*calls):
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
    for i, name in enumerate(calls, start=2):
        lines.append(
            {
                "jsonrpc": "2.0",
                "id": i,
                "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            }
        )
    return io.StringIO("".join(json.dumps(line) + "\n" for line in lines))


def _serve(*calls, **kwargs):
    out = io.StringIO()
    assert serve(Root, stdin=_requests(*calls), stdout=out, **kwargs) == 0
    return [json.loads(line) for line in out.getvalue().splitlines()][1:]


def test_serve_sends_a_tool_calls_bytes_to_the_variables_descriptor(
    tmp_path, monkeypatch
):
    path = tmp_path / "served.bin"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | BINARY_FLAG)
    os.set_inheritable(fd, True)
    monkeypatch.setenv("BIN_MCP_OUTPUT", str(fd))
    try:
        first, second = _serve("bin.write-bytes", "bin.write-bytes")
    finally:
        os.close(fd)
    assert path.read_bytes() == BLOB + BLOB
    assert first["result"]["content"][0]["text"].endswith(", at offset 0")
    assert second["result"]["content"][0]["text"].endswith(", at offset 10")


def test_serve_appends_to_a_named_file_across_calls_and_names_it(tmp_path, monkeypatch):
    path = tmp_path / "appended.bin"
    path.write_bytes(b"0123")
    monkeypatch.setenv("BIN_MCP_OUTPUT", str(path))
    first, second = _serve("bin.write-bytes", "bin.write-bytes")
    assert path.read_bytes() == b"0123" + BLOB + BLOB
    assert first["result"]["content"][0]["text"] == (
        "10 bytes of output that is not text were written to file descriptor "
        "%d (%s), at offset 4" % (_fd_in(first), path)
    )
    assert second["result"]["content"][0]["text"].endswith("(%s), at offset 14" % path)


def _fd_in(reply):
    text = reply["result"]["content"][0]["text"]
    return int(text.split("file descriptor ")[1].split(" ")[0])


def test_serve_with_a_file_that_cannot_be_opened_exits_2_before_any_request(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("BIN_MCP_OUTPUT", str(tmp_path / "no" / "dir.bin"))
    out = io.StringIO()
    assert serve(Root, stdin=_requests("bin.texty"), stdout=out) == 2
    assert out.getvalue() == ""
    assert "BIN_MCP_OUTPUT" in capsys.readouterr().err


def test_serve_with_a_closed_descriptor_variable_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("BIN_MCP_OUTPUT", str(_closed_fd()))
    assert serve(Root, stdin=_requests(), stdout=io.StringIO()) == 2
    assert "BIN_MCP_OUTPUT" in capsys.readouterr().err


def test_serve_names_the_variable_it_is_told_to(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("TOOL_MCP_OUTPUT", str(tmp_path / "no" / "dir.bin"))
    out = io.StringIO()
    code = serve(Root, stdin=_requests(), stdout=out, output_var="TOOL_MCP_OUTPUT")
    assert code == 2
    assert "TOOL_MCP_OUTPUT" in capsys.readouterr().err


_APP = textwrap.dedent("""
    import sys

    import duho
    from duho import Cli, Cmd


    class Emit(Cmd):
        \"\"\"Writes bytes.\"\"\"

        def __call__(self):
            sys.stdout.buffer.write(b"\\xff\\x00data")
            return 0


    class Tool(Cli):
        \"\"\"Root.\"\"\"

        _parsername_ = "tool"
        _subcommands_ = [Emit]


    if __name__ == "__main__":
        sys.exit(duho.main(Tool))
    """)


def _run_server(tmp_path, redirect=None, extra_env=None):
    script = tmp_path / "toolapp.py"
    script.write_bytes(_APP.encode("utf-8"))
    env = subprocess_env(extra={"TOOL_MCP": "stdio", **(extra_env or {})})
    requests = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "tool.emit", "arguments": {}},
            }
        )
        + "\n"
    )
    if redirect is None:
        command = [sys.executable, str(script)]
    else:
        command = [
            "sh",
            "-c",
            'exec "$0" "$1" ' + redirect,
            sys.executable,
            str(script),
        ]
    return subprocess.run(
        command,
        input=requests,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_a_real_server_started_with_a_file_name_writes_the_file(tmp_path):
    target = tmp_path / "named.bin"
    done = _run_server(tmp_path, extra_env={"TOOL_MCP_OUTPUT": str(target)})
    assert done.returncode == 0, done.stderr
    assert target.read_bytes() == b"\xff\x00data"
    text = json.loads(done.stdout.splitlines()[1])["result"]["content"][0]["text"]
    assert text.startswith("6 bytes of output that is not text were written to file")
    assert "(%s), at offset 0" % target in text


def test_a_real_server_with_a_file_that_cannot_be_opened_exits_2(tmp_path):
    bad = tmp_path / "no" / "dir.bin"
    done = _run_server(tmp_path, extra_env={"TOOL_MCP_OUTPUT": str(bad)})
    assert done.returncode == 2
    assert done.stdout == ""
    assert "TOOL_MCP_OUTPUT" in done.stderr


@NOT_POSIX
def test_a_real_server_started_with_descriptor_3_writes_the_file(tmp_path):
    target = tmp_path / "three.bin"
    done = _run_server(tmp_path, '3>"%s"' % target)
    assert done.returncode == 0, done.stderr
    assert target.read_bytes() == b"\xff\x00data"
    text = json.loads(done.stdout.splitlines()[1])["result"]["content"][0]["text"]
    assert text.startswith(
        "6 bytes of output that is not text were written to file descriptor 3"
    )
    if sys.platform.startswith("linux"):
        assert "(%s)" % os.path.realpath(str(target)) in text
    assert text.endswith(", at offset 0")


@NOT_POSIX
def test_a_real_server_started_with_the_variable_uses_that_descriptor(tmp_path):
    target = tmp_path / "seven.bin"
    done = _run_server(tmp_path, '7>"%s"' % target, {"TOOL_MCP_OUTPUT": "7"})
    assert done.returncode == 0, done.stderr
    assert target.read_bytes() == b"\xff\x00data"


@NOT_POSIX
def test_a_real_server_with_no_descriptor_answers_the_error(tmp_path):
    done = _run_server(tmp_path, "3>&-")
    assert done.returncode == 0, done.stderr
    reply = json.loads(done.stdout.splitlines()[1])
    assert reply["result"]["isError"] is True
    assert reply["result"]["content"][0]["text"].endswith(
        "name an open one in TOOL_MCP_OUTPUT"
    )


# --------------------------------------------------------------------------
# A named file that is a pipe
# --------------------------------------------------------------------------

import threading
import uuid

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows named pipes")


def _drain(read_once, sink):
    while True:
        try:
            chunk = read_once()
        except OSError:
            return
        if not chunk:
            return
        sink.append(chunk)


def _two_calls_text(monkeypatch, name):
    monkeypatch.setenv("BIN_MCP_OUTPUT", name)
    first, second = _serve("bin.write-bytes", "bin.write-bytes")
    return (
        first["result"]["content"][0]["text"],
        second["result"]["content"][0]["text"],
    )


@WINDOWS_ONLY
def test_a_windows_named_pipe_receives_the_bytes_and_the_real_count(monkeypatch):
    import _winapi

    bs = chr(92)
    name = "%s%s.%spipe%sduho_%d_%s" % (bs, bs, bs, bs, os.getpid(), uuid.uuid4().hex)
    assert name.startswith(bs + bs + "." + bs + "pipe" + bs)
    handle = _winapi.CreateNamedPipe(
        name, _winapi.PIPE_ACCESS_INBOUND, 0, 1, 65536, 65536, 0, 0
    )
    received = []

    def read():
        try:
            _winapi.ConnectNamedPipe(handle, overlapped=False)
        except OSError as exc:
            if getattr(exc, "winerror", None) != 535:
                return
        _drain(lambda: _winapi.ReadFile(handle, 65536)[0], received)

    reader = threading.Thread(target=read)
    reader.start()
    try:
        first, second = _two_calls_text(monkeypatch, name)
    finally:
        reader.join(30)
        _winapi.CloseHandle(handle)
    assert not reader.is_alive()
    assert b"".join(received) == BLOB + BLOB
    assert first.startswith("10 bytes of output that is not text were written")
    assert "(%s), at offset 0 of this server's output" % name in first
    assert second.startswith("10 bytes")
    assert second.endswith(", at offset 10 of this server's output")


@NOT_POSIX
def test_a_fifo_receives_the_bytes_and_the_real_count(tmp_path, monkeypatch):
    fifo = str(tmp_path / "out.fifo")
    os.mkfifo(fifo)
    reader_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    os.set_blocking(reader_fd, True)
    keep_open = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    received = []
    reader = threading.Thread(
        target=_drain, args=(lambda: os.read(reader_fd, 65536), received)
    )
    reader.start()
    try:
        first, second = _two_calls_text(monkeypatch, fifo)
    finally:
        os.close(keep_open)
        reader.join(30)
        os.close(reader_fd)
    assert not reader.is_alive()
    assert b"".join(received) == BLOB + BLOB
    assert first.startswith("10 bytes of output that is not text were written")
    assert first.endswith("(%s), at offset 0 of this server's output" % fifo)
    assert second.endswith(", at offset 10 of this server's output")


@NOT_POSIX
def test_a_fifo_with_no_reader_stops_the_server_instead_of_hanging(tmp_path):
    fifo = tmp_path / "noreader.fifo"
    os.mkfifo(str(fifo))
    done = _run_server(tmp_path, extra_env={"TOOL_MCP_OUTPUT": str(fifo)})
    assert done.returncode == 2
    assert done.stdout == ""
    assert "TOOL_MCP_OUTPUT" in done.stderr


def test_a_named_pipe_that_does_not_exist_stops_the_server(tmp_path):
    bs = chr(92)
    name = "%s%s.%spipe%sduho_missing_%s" % (bs, bs, bs, bs, uuid.uuid4().hex)
    target = name if os.name == "nt" else str(tmp_path / "no" / "pipe")
    done = _run_server(tmp_path, extra_env={"TOOL_MCP_OUTPUT": target})
    assert done.returncode == 2
    assert "TOOL_MCP_OUTPUT" in done.stderr
