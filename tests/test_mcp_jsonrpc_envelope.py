"""The JSON-RPC envelope is validated as JSON-RPC 2.0 and MCP describe it."""

import io
import json

import pytest

from duho import Cli, Cmd
from duho.mcp import call_tool, serve


class Count(Cmd):
    """Echo an integer."""

    n: int = 0
    "a number"
    ("--n",)

    def __call__(self):
        return {"n": self.n}


class Root(Cli):
    """Root."""

    _parsername_ = "root"
    _subcommands_ = [Count]


def _exchange(*lines):
    out = io.StringIO()
    serve(Root, stdin=io.StringIO("".join(line + "\n" for line in lines)), stdout=out)
    return [line for line in out.getvalue().splitlines() if line]


def _error_code(line):
    return json.loads(line)["error"]["code"]


def test_nan_id_is_a_parse_error_and_every_reply_is_valid_json():
    (reply,) = _exchange('{"jsonrpc":"2.0","id":NaN,"method":"ping"}')
    assert _error_code(reply) == -32700
    json.loads(reply, parse_constant=pytest.fail)


@pytest.mark.parametrize(
    "request_line",
    [
        '{"id":1,"method":"ping"}',
        '{"jsonrpc":"1.0","id":1,"method":"ping"}',
        '{"jsonrpc":"2.0","id":{"a":1},"method":"ping"}',
        '{"jsonrpc":"2.0","id":true,"method":"ping"}',
        '{"jsonrpc":"2.0","id":null,"method":"ping"}',
    ],
)
def test_malformed_envelope_is_invalid_request(request_line):
    (reply,) = _exchange(request_line)
    assert _error_code(reply) == -32600


def test_client_response_object_is_not_answered():
    assert _exchange('{"jsonrpc":"2.0","id":9,"result":{}}') == []


def test_well_formed_request_is_still_answered():
    (reply,) = _exchange('{"jsonrpc":"2.0","id":"a","method":"ping"}')
    assert json.loads(reply) == {"jsonrpc": "2.0", "id": "a", "result": {}}


def test_integral_float_is_accepted_for_an_integer_field():
    result = call_tool(Root, "root.count", {"n": 5.0})
    assert "isError" not in result
    assert json.loads(result["content"][0]["text"]) == {"n": 5}


def test_fractional_float_is_still_refused_for_an_integer_field():
    with pytest.raises(ValueError):
        call_tool(Root, "root.count", {"n": 5.5})
