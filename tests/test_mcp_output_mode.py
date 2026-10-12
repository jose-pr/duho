"""``_mcp_output_``: where a command's standard output goes as an MCP tool."""

import base64
import io
import json
import os
import sys

import pytest

import duho
from duho import Cli, Cmd
from duho.mcp import _core_for_app, call_tool, describe_tools, serve

BLOB = bytes([255, 254, 0, 98, 105, 110, 128])
RAN = []


class AutoText(Cmd):
    """Prints text; the default mode."""

    def __call__(self):
        print("plain text")
        return 0


class AutoNone(Cmd):
    """Names the default explicitly."""

    _mcp_output_ = None

    def __call__(self):
        sys.stdout.buffer.write(BLOB)
        return 0


class StreamText(Cmd):
    """Streams text."""

    _mcp_output_ = "stream"

    def __call__(self):
        RAN.append("stream-text")
        print("only for the target")
        return 0


class StreamFails(Cmd):
    """Streams, then exits non-zero."""

    _mcp_output_ = "stream"

    def __call__(self):
        print("x")
        return 3


class StreamBytes(Cmd):
    """Streams bytes."""

    _mcp_output_ = "stream"

    def __call__(self):
        sys.stdout.buffer.write(BLOB)
        return 0


class ResultBytes(Cmd):
    """Returns bytes in the result."""

    _mcp_output_ = "result"

    def __call__(self):
        sys.stdout.buffer.write(BLOB)
        return 0


class ResultBytesFail(Cmd):
    """Returns bytes in the result and exits non-zero."""

    _mcp_output_ = "result"

    def __call__(self):
        sys.stdout.buffer.write(BLOB)
        return 2


class ResultText(Cmd):
    """Returns text in the result."""

    _mcp_output_ = "result"

    def __call__(self):
        print("kept", end="")
        sys.stdout.buffer.write(b" as is\n")
        return 0


class Modes(Cli):
    """Root."""

    _parsername_ = "modes"
    _subcommands_ = [
        AutoText,
        AutoNone,
        StreamText,
        StreamFails,
        StreamBytes,
        ResultBytes,
        ResultBytesFail,
        ResultText,
    ]


def _call(name, **kwargs):
    return call_tool(Modes, "modes." + name, {}, **kwargs)


def _target(tmp_path, name="target.bin"):
    path = tmp_path / name
    fd = os.open(
        str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    )
    return path, fd


def _texts(result):
    return [item["text"] for item in result["content"] if item["type"] == "text"]


def test_auto_and_none_are_the_default():
    assert [_texts(_call("auto-text"))] == [["plain text\n"]]
    result = _call("auto-none")
    assert result["isError"] is True


def test_stream_sends_text_only_to_the_target_and_not_to_the_result(tmp_path):
    path, fd = _target(tmp_path)
    try:
        result = _call("stream-text", output_fd=fd, output_path=str(path))
    finally:
        os.close(fd)
    assert path.read_bytes() == b"only for the target\n"
    assert result == {
        "content": [
            {
                "type": "text",
                "text": "20 bytes of output that is not text were written to file "
                "descriptor %d (%s), at offset 0" % (fd, path),
            }
        ]
    }


def test_stream_reports_the_exit_code_after_the_line(tmp_path):
    path, fd = _target(tmp_path)
    try:
        result = _call("stream-fails", output_fd=fd, output_path=str(path))
    finally:
        os.close(fd)
    assert result["isError"] is True
    lines = _texts(result)[0].split("\n")
    assert lines[0].startswith("2 bytes of output that is not text were written")
    assert lines[1:] == ["exit code: 3"]


def test_stream_with_bytes_and_no_target_does_not_run_the_command():
    RAN.clear()
    result = _call("stream-text", output_var="MODES_MCP_OUTPUT")
    assert result["isError"] is True
    assert RAN == []
    assert _texts(result) == [
        "this tool sends its output to the server's output target, and this "
        "server has none: start it with file descriptor 3 open, or name an open "
        "one in MODES_MCP_OUTPUT"
    ]


def test_stream_sends_bytes_to_the_target(tmp_path):
    path, fd = _target(tmp_path)
    try:
        _call("stream-bytes", output_fd=fd, output_path=str(path))
    finally:
        os.close(fd)
    assert path.read_bytes() == BLOB


def test_result_returns_bytes_as_a_resource_and_leaves_the_target_alone(tmp_path):
    path, fd = _target(tmp_path)
    try:
        result = _call("result-bytes", output_fd=fd, output_path=str(path))
    finally:
        os.close(fd)
    assert path.read_bytes() == b""
    assert "isError" not in result
    text, resource = result["content"]
    assert text == {
        "type": "text",
        "text": "7 bytes of output that is not text follow as a base64 resource",
    }
    assert resource["type"] == "resource"
    body = resource["resource"]
    assert body["uri"] == "duho://output/modes.result-bytes"
    assert body["mimeType"] == "application/octet-stream"
    assert base64.b64decode(body["blob"]) == BLOB


def test_result_bytes_carry_the_exit_code_in_the_text_item():
    result = _call("result-bytes-fail")
    assert result["isError"] is True
    text, resource = result["content"]
    assert text["text"].split("\n") == [
        "7 bytes of output that is not text follow as a base64 resource",
        "exit code: 2",
    ]
    assert base64.b64decode(resource["resource"]["blob"]) == BLOB


def test_result_text_is_the_text_item_even_if_written_through_the_buffer(tmp_path):
    path, fd = _target(tmp_path)
    try:
        result = _call("result-text", output_fd=fd)
    finally:
        os.close(fd)
    assert result == {"content": [{"type": "text", "text": "kept as is\n"}]}
    assert path.read_bytes() == b""


def test_a_bad_value_is_a_value_error_naming_the_command():
    class Bad(Cmd):
        """Has a bad mode."""

        _mcp_output_ = "everything"

        def __call__(self):  # pragma: no cover
            return 0

    class BadRoot(Cli):
        """Root."""

        _parsername_ = "badroot"
        _subcommands_ = [Bad]

    with pytest.raises(ValueError, match="badroot.bad"):
        describe_tools(BadRoot)


def test_a_non_string_value_is_a_value_error():
    class Worse(Cmd):
        """Has a bad mode."""

        _mcp_output_ = True

        def __call__(self):  # pragma: no cover
            return 0

    class WorseRoot(Cli):
        """Root."""

        _parsername_ = "worseroot"
        _subcommands_ = [Worse]

    with pytest.raises(ValueError, match="worseroot.worse"):
        describe_tools(WorseRoot)


def _lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_served_resource_item_reaches_the_client_unchanged():
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "modes.result-bytes", "arguments": {}},
            }
        )
        + "\n"
    )
    out = io.StringIO()
    assert serve(Modes, stdin=stdin, stdout=out) == 0
    result = _lines(out.getvalue())[1]["result"]
    assert result["content"][1] == {
        "type": "resource",
        "resource": {
            "uri": "duho://output/modes.result-bytes",
            "mimeType": "application/octet-stream",
            "blob": base64.b64encode(BLOB).decode("ascii"),
        },
    }


# --------------------------------------------------------------------------
# Module commands: a module-level name
# --------------------------------------------------------------------------

_STREAM_MODULE = """\
import sys

_mcp_output_ = "stream"


def main(args=None):
    print("module text")
    return 0
"""

_RESULT_MODULE = """\
import sys

_mcp_output_ = "result"


def main(args=None):
    sys.stdout.buffer.write(bytes([255, 254, 0, 98, 105, 110, 128]))
    return 0
"""

_AUTO_MODULE = """\
import sys

_mcp_output_ = "auto"


def main(args=None):
    print("auto module")
    return 0
"""

_BAD_MODULE = """\
_mcp_output_ = "sometimes"


def main(args=None):
    return 0
"""


class ModuleRoot(duho.Cmd):
    """Root of a module-command tree."""

    _parsername_ = "mods"

    def __call__(self):  # pragma: no cover - a namespace
        return 0


def _module_core(tmp_path, **sources):
    for name, source in sources.items():
        (tmp_path / (name + ".py")).write_bytes(source.encode("utf-8"))
    return _core_for_app(ModuleRoot, source=tmp_path, argv=[])


def test_module_command_stream(tmp_path):
    core = _module_core(tmp_path, streamer=_STREAM_MODULE)
    path, fd = _target(tmp_path, "m.bin")
    try:
        result = call_tool(core, "mods.streamer", {}, output_fd=fd)
        no_target = call_tool(core, "mods.streamer", {})
    finally:
        os.close(fd)
    assert path.read_bytes() == b"module text\n"
    assert _texts(result)[0].startswith("12 bytes of output that is not text")
    assert no_target["isError"] is True
    assert "sends its output to the server's output target" in _texts(no_target)[0]


def test_module_command_result(tmp_path):
    core = _module_core(tmp_path, resulter=_RESULT_MODULE)
    result = call_tool(core, "mods.resulter", {})
    assert base64.b64decode(result["content"][1]["resource"]["blob"]) == BLOB


def test_module_command_auto(tmp_path):
    core = _module_core(tmp_path, automatic=_AUTO_MODULE)
    result = call_tool(core, "mods.automatic", {})
    assert _texts(result) == ["auto module\n"]


def test_module_command_bad_value_names_the_command(tmp_path):
    with pytest.raises(ValueError, match="mods.bad"):
        core = _module_core(tmp_path, bad=_BAD_MODULE)
        describe_tools(core)
