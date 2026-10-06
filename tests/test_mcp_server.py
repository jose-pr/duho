"""Tests for the ``duho.mcp`` stdio JSON-RPC server loop.

Drives ``duho.mcp.serve`` over in-memory ``io.StringIO`` streams with a
scripted ``initialize`` -> ``notifications/initialized`` -> ``tools/list`` ->
``tools/call`` conversation and asserts the JSON-RPC responses. Also checks
the two zero-eager-import guard tests are untouched by this module's
existence (``duho.mcp`` is never imported by ``duho/__init__.py``, so a plain
``import duho`` must still never load ``json``/``importlib.metadata``).

Fixtures at module level (AST-based introspection needs a real source file).
"""

import io
import json
import subprocess
import sys

import pytest

from conftest import subprocess_env
from duho import Cli, Cmd
from duho.mcp import _resolve_app, main, serve


class Ping(Cmd):
    """Reply pong."""

    def __call__(self):
        print("pong")
        return 0


class Server(Cli):
    """A tiny app for server tests."""

    _parsername_ = "server"

    _version_ = "0.0.1"
    _subcommands_ = [Ping]


def _lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _run(*requests):
    stdin = io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n")
    stdout = io.StringIO()
    rc = serve(Server, stdin=stdin, stdout=stdout)
    return rc, _lines(stdout.getvalue())


# --------------------------------------------------------------------------
# Scripted conversation
# --------------------------------------------------------------------------


def test_initialize_responds_with_protocol_and_server_info():
    rc, responses = _run(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
    )
    assert rc == 0
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"] == {"tools": {}}
    # serverInfo reports the served APP's own identity, not a fixed
    # "duho.mcp"/duho version: Server declares no _parsername_ (so its
    # name resolves to its class name) but DOES declare its own
    # _version_, which must be reported verbatim.
    assert result["serverInfo"]["name"] == "server"
    assert result["serverInfo"]["version"] == "0.0.1"


def test_notification_gets_no_response():
    rc, responses = _run(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    # Exactly two responses: initialize + tools/list. The notification in
    # between produced NOTHING (JSON-RPC forbids replying to a notification).
    assert [r["id"] for r in responses] == [1, 2]


def test_tools_list_returns_the_describe_tools_shape():
    rc, responses = _run(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    tools = responses[0]["result"]["tools"]
    names = {t["name"] for t in tools}
    # "Server" itself is a namespace (its own subcommand is mandatory), so it
    # is not listed as a callable tool.
    assert names == {"server.ping"}
    for tool in tools:
        assert set(tool) == {"name", "description", "inputSchema"}


def test_tools_call_dispatches_and_returns_call_tool_result():
    rc, responses = _run(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "server.ping", "arguments": {}},
        },
    )
    result = responses[0]["result"]
    assert result["content"][0]["text"] == "pong\n"
    assert result.get("isError") is not True


def test_full_scripted_conversation():
    rc, responses = _run(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "server.ping", "arguments": {}},
        },
    )
    assert rc == 0
    ids = [r["id"] for r in responses]
    assert ids == [1, 2, 3]
    assert responses[2]["result"]["content"][0]["text"] == "pong\n"


# --------------------------------------------------------------------------
# Protocol-level errors
# --------------------------------------------------------------------------


def test_malformed_json_line_gets_parse_error():
    stdin = io.StringIO("not json at all\n")
    stdout = io.StringIO()
    serve(Server, stdin=stdin, stdout=stdout)
    responses = _lines(stdout.getvalue())
    assert responses[0]["error"]["code"] == -32700


def test_unknown_method_gets_method_not_found():
    rc, responses = _run({"jsonrpc": "2.0", "id": 9, "method": "bogus/method"})
    assert responses[0]["error"]["code"] == -32601


def test_blank_lines_are_skipped():
    stdin = io.StringIO(
        "\n\n"
        + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        + "\n\n"
    )
    stdout = io.StringIO()
    serve(Server, stdin=stdin, stdout=stdout)
    responses = _lines(stdout.getvalue())
    assert len(responses) == 1


# --------------------------------------------------------------------------
# EOF -> clean shutdown
# --------------------------------------------------------------------------


def test_serve_returns_zero_on_stdin_eof():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    assert serve(Server, stdin=stdin, stdout=stdout) == 0


# --------------------------------------------------------------------------
# App resolution (the `<app>` CLI argument)
# --------------------------------------------------------------------------


def test_resolve_app_accepts_colon_syntax():
    resolved = _resolve_app(__name__ + ":Server")
    assert resolved is Server


def test_resolve_app_rejects_non_cmd_target():
    with pytest.raises(TypeError):
        _resolve_app(__name__ + ":_lines")


def test_main_reports_unresolvable_app():
    # `main` repoints the process's fds 0 and 1 before resolving the app, so
    # it runs in a child process rather than in the test session.
    proc = subprocess.run(
        [sys.executable, "-m", "duho.mcp", "no.such.module:Nope"],
        capture_output=True,
        text=True,
        env=subprocess_env(),
        timeout=60,
    )
    assert proc.returncode == 1
    assert "could not resolve app" in proc.stderr
    assert proc.stdout == ""


def test_main_closes_the_protocol_streams_when_the_app_does_not_resolve(monkeypatch):
    stream_in, stream_out = io.BytesIO(), io.StringIO()
    monkeypatch.setattr(
        "duho.mcp._cli._real_stdio_streams", lambda: (stream_in, stream_out)
    )
    assert main(["no.such.module:Nope"]) == 1
    assert stream_in.closed
    assert stream_out.closed


def test_main_with_no_args_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage" in captured.err


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_main_help_flag_prints_usage_to_stdout_without_resolving(capsys, flag):
    with pytest.raises(SystemExit) as excinfo:
        main([flag])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "usage" in captured.out
    assert "could not resolve" not in captured.err


# --------------------------------------------------------------------------
# python -m duho.mcp <app> end-to-end (real subprocess)
# --------------------------------------------------------------------------


def test_python_dash_m_end_to_end(tmp_path):
    app_file = tmp_path / "mcp_e2e_app.py"
    app_file.write_text(
        "from duho import Cli, Cmd\n"
        "\n"
        "class Ping(Cmd):\n"
        '    """Reply pong."""\n'
        "    def __call__(self):\n"
        '        print("pong")\n'
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """E2E app."""\n'
        '    _parsername_ = "app"\n'
        "    _subcommands_ = [Ping]\n"
    )
    request = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        + "\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "duho.mcp", "mcp_e2e_app:App"],
        input=request,
        capture_output=True,
        text=True,
        env=subprocess_env(extra_path=tmp_path),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    responses = _lines(proc.stdout)
    names = {t["name"] for t in responses[0]["result"]["tools"]}
    assert names == {"app.ping"}


# --------------------------------------------------------------------------
# Zero-eager-import contract: duho.mcp existing must not affect `import duho`
# --------------------------------------------------------------------------


def test_duho_agenthelp_is_lazy_until_first_attribute_access():
    code = (
        "import sys, duho\n"
        "print('duho.agenthelp' in sys.modules)\n"
        "duho.agenthelp\n"
        "print('duho.agenthelp' in sys.modules)\n"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    ).splitlines()
    assert out == ["False", "True"]


def test_plain_import_duho_still_lazy_about_json():
    code = "import sys, duho; print('json' in sys.modules)"
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    )
    assert out.strip() == "False"


def test_plain_import_duho_still_lazy_about_importlib_metadata():
    code = "import sys, duho; print('importlib.metadata' in sys.modules)"
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    )
    assert out.strip() == "False"


def test_import_duho_mcp_alone_does_not_load_json():
    code = "import sys, duho.mcp; print('json' in sys.modules)"
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    )
    assert out.strip() == "False"
