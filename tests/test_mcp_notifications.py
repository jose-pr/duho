"""A request without an ``id`` is a notification and is never answered."""

import io
import json

import pytest

from duho import Cli, Cmd
from duho.mcp import serve


class Hello(Cmd):
    """Say hello."""

    def __call__(self):
        return {"hello": True}


class Root(Cli):
    """Root."""

    _parsername_ = "root"
    _subcommands_ = [Hello]


@pytest.mark.parametrize(
    "request_body",
    [
        {"method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"method": "ping"},
        {"method": "tools/list"},
        {"method": "tools/call", "params": {"name": "root.hello", "arguments": {}}},
        {"method": "shutdown"},
        {"method": "exit"},
        {"method": "no/such-method"},
    ],
)
def test_request_without_an_id_gets_no_reply(request_body):
    line = json.dumps(dict(request_body, jsonrpc="2.0")) + "\n"
    out = io.StringIO()
    assert serve(Root, stdin=io.StringIO(line), stdout=out) == 0
    assert out.getvalue() == ""
