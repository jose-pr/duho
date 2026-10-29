"""``serve(..., tools=)`` and a root's ``_mcp_tools_``: a ``ToolProvider``'s tools
served beside the command tools, over in-memory streams.

Fixtures at module level (AST-based introspection needs a real source file).
"""

import io
import json
import logging

from duho import Cli, Cmd
from duho.mcp import ToolProvider, serve


class Ping(Cmd):
    """Reply pong."""

    def __call__(self):
        print("pong")
        return 0


class Server(Cli):
    """A tiny app for provider tests."""

    _parsername_ = "server"
    _subcommands_ = [Ping]


def _tool(name):
    return {
        "name": name,
        "description": "A provider tool.",
        "inputSchema": {"type": "object", "properties": {}},
    }


class Recorder:
    """A provider that records its calls; ``extra.refresh`` sends a notification."""

    def __init__(self, notify, names=("extra.echo", "extra.refresh")):
        self.notify = notify
        self.names = list(names)
        self.calls = []
        self.closed = 0

    def list_tools(self):
        return [_tool(name) for name in self.names]

    def call_tool(self, name, arguments):
        if name not in self.names:
            return None
        self.calls.append((name, arguments))
        if name == "extra.refresh":
            self.notify("notifications/tools/list_changed")
        return {"content": [{"type": "text", "text": "from %s" % name}]}

    def close(self):
        self.closed += 1


class WithTools(Server):
    """The same app, its provider declared on the root."""

    _parsername_ = "server"
    _mcp_tools_ = staticmethod(lambda notify: Recorder(notify, names=["extra.echo"]))


def _initialize(protocol="2025-06-18"):
    return {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }


def _call(req_id, name, arguments=None):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": params}


def _run(root, *requests, tools=None):
    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    stdout = io.StringIO()
    rc = serve(root, stdin=stdin, stdout=stdout, tools=tools)
    return rc, [
        json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()
    ]


def _factory(made, **kwargs):
    def factory(notify):
        provider = Recorder(notify, **kwargs)
        made.append(provider)
        return provider

    return factory


def test_recorder_satisfies_the_protocol():
    # A structural check: the test double has every member the contract names.
    for member in ("list_tools", "call_tool", "close"):
        assert callable(getattr(Recorder, member))
        assert hasattr(ToolProvider, member)


def test_provider_tools_follow_the_command_tools_and_are_called():
    made = []
    rc, (init, listed, echoed, ping) = _run(
        Server,
        _initialize(),
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        _call(2, "extra.echo", {"x": 1}),
        _call(3, "server.ping", {}),
        tools=_factory(made),
    )
    assert rc == 0
    assert init["result"]["capabilities"]["tools"] == {"listChanged": True}
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert names[-2:] == ["extra.echo", "extra.refresh"]
    assert "server.ping" in names
    assert echoed["result"]["content"][0]["text"] == "from extra.echo"
    assert ping["result"]["content"][0]["text"] == "pong\n"
    assert made[0].calls == [("extra.echo", {"x": 1})]
    assert made[0].closed == 1


def test_notify_writes_before_the_response_of_the_call_that_sent_it():
    _, (_, note, response) = _run(
        Server, _initialize(), _call(1, "extra.refresh"), tools=_factory([])
    )
    assert note == {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
    assert response["id"] == 1


def test_a_name_neither_resolves_is_unknown_and_bad_arguments_are_invalid():
    _, (_, unknown, bad) = _run(
        Server,
        _initialize(),
        _call(1, "extra.missing", {}),
        _call(2, "extra.echo", ["not", "an", "object"]),
        tools=_factory([]),
    )
    assert unknown["error"]["code"] == -32602
    assert bad["error"]["code"] == -32602


def test_a_provider_tool_named_like_a_command_tool_is_dropped_once(caplog):
    made = []
    with caplog.at_level(logging.WARNING, logger="duho.mcp"):
        _, (_, first, second, ping) = _run(
            Server,
            _initialize(),
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            _call(3, "server.ping", {}),
            tools=_factory(made, names=["server.ping", "extra.echo"]),
        )
    for listed in (first, second):
        names = [tool["name"] for tool in listed["result"]["tools"]]
        assert names.count("server.ping") == 1
        assert "extra.echo" in names
    # The command tool answers; the provider is never asked for the name.
    assert ping["result"]["content"][0]["text"] == "pong\n"
    assert made[0].calls == []
    assert len([r for r in caplog.records if "server.ping" in r.getMessage()]) == 1


def test_a_provider_failure_is_an_internal_error_and_the_loop_goes_on():
    class Broken(Recorder):
        def call_tool(self, name, arguments):
            if name == "extra.echo":
                raise RuntimeError("peer went away")
            return "not a dict" if name == "extra.refresh" else None

    _, (_, raised, wrong, ping) = _run(
        Server,
        _initialize(),
        _call(1, "extra.echo", {}),
        _call(2, "extra.refresh", {}),
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        tools=lambda notify: Broken(notify),
    )
    assert raised["error"]["code"] == -32603
    assert "peer went away" in raised["error"]["message"]
    assert wrong["error"]["code"] == -32603
    assert ping["result"] == {}


def test_a_factory_that_raises_ends_serve_with_status_2(capsys):
    def factory(notify):
        raise ValueError("bad declaration")

    rc, responses = _run(Server, _initialize(), tools=factory)
    assert rc == 2
    assert responses == []
    assert "bad declaration" in capsys.readouterr().err


def test_the_root_attribute_is_the_default_and_no_provider_declares_nothing():
    _, (init, listed) = _run(
        WithTools, _initialize(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert init["result"]["capabilities"]["tools"] == {"listChanged": True}
    assert "extra.echo" in [tool["name"] for tool in listed["result"]["tools"]]

    _, (plain,) = _run(Server, _initialize())
    assert plain["result"]["capabilities"]["tools"] == {}
