"""Protocol revision negotiation and the version-dependent bad-argument reply."""

from __future__ import annotations

import io
import json

import pytest

from duho import Cli, Cmd
from duho.mcp import InvalidArgumentsError, call_tool, serve


class Echo(Cmd):
    """Echo a word."""

    word: str

    def __call__(self):
        print(self.word)


class Root(Cli):
    """Root."""

    _parsername_ = "root"
    _subcommands_ = [Echo]


def _run(*requests):
    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    stdout = io.StringIO()
    assert serve(Root, stdin=stdin, stdout=stdout) == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def _init(version, req_id=1):
    params = {"protocolVersion": version} if version else {}
    return {"jsonrpc": "2.0", "id": req_id, "method": "initialize", "params": params}


def _call(name, arguments, req_id=2):
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


@pytest.mark.parametrize(
    "requested,expected",
    [
        ("2025-11-25", "2025-11-25"),
        ("2025-06-18", "2025-06-18"),
        ("2025-03-26", "2025-03-26"),
        ("2024-11-05", "2024-11-05"),
        ("2099-01-01", "2025-11-25"),
        ("not-a-version", "2025-11-25"),
        (None, "2025-11-25"),
    ],
)
def test_initialize_answers_the_requested_or_newest_revision(requested, expected):
    (reply,) = _run(_init(requested))
    assert reply["result"]["protocolVersion"] == expected


# The reply the handler produced before 2025-11-25 was supported.
_BAD_ARGUMENT_ERROR = {
    "jsonrpc": "2.0",
    "id": 2,
    "error": {"code": -32602, "message": "missing required argument 'word'"},
}


def _bad_argument_message():
    return "missing required argument 'word'"


@pytest.mark.parametrize("version", ["2024-11-05", "2025-03-26", "2025-06-18"])
def test_bad_arguments_are_a_json_rpc_error_up_to_2025_06_18(version):
    _, reply = _run(_init(version), _call("root.echo", {}))
    assert reply == _BAD_ARGUMENT_ERROR


def test_bad_arguments_are_a_json_rpc_error_before_initialize():
    (reply,) = _run(_call("root.echo", {}))
    assert reply == _BAD_ARGUMENT_ERROR


def test_bad_arguments_are_a_tool_result_from_2025_11_25():
    _, reply = _run(_init("2025-11-25"), _call("root.echo", {}))
    assert reply == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "content": [{"type": "text", "text": _bad_argument_message()}],
            "isError": True,
        },
    }


def test_an_unknown_property_is_a_tool_result_from_2025_11_25():
    _, reply = _run(_init("2025-11-25"), _call("root.echo", {"word": "a", "nope": 1}))
    assert reply["result"]["isError"] is True
    assert "nope" in reply["result"]["content"][0]["text"]


def test_a_valid_call_is_unchanged_in_2025_11_25():
    _, reply = _run(_init("2025-11-25"), _call("root.echo", {"word": "hi"}))
    assert reply["result"]["content"][0]["text"] == "hi\n"
    assert "isError" not in reply["result"]


@pytest.mark.parametrize("version", ["2024-11-05", "2025-06-18", "2025-11-25"])
def test_an_unknown_tool_is_a_json_rpc_error_in_every_revision(version):
    _, reply = _run(_init(version), _call("root.nope", {}))
    assert reply["error"]["code"] == -32602
    assert "result" not in reply


def test_non_object_arguments_stay_a_json_rpc_error_in_2025_11_25():
    _, reply = _run(_init("2025-11-25"), _call("root.echo", "word"))
    assert reply["error"]["code"] == -32602


def test_a_newer_session_does_not_leak_into_the_next_serve():
    _run(_init("2025-11-25"))
    (reply,) = _run(_call("root.echo", {}))
    assert reply["error"]["code"] == -32602


def test_the_python_call_tool_still_raises_for_bad_arguments():
    with pytest.raises(InvalidArgumentsError):
        call_tool(Root, "root.echo", {})
