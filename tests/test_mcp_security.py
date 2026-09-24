"""Regression tests for the 2026-09-24 in-depth review's MCP findings.

Covers argv-injection safety, env/config layering, dispatch through the root
parser (root globals reachable from a nested tool), JSON-RPC envelope
hardening, protocol negotiation, and the stdio isolation that must be proven
against a REAL ``python -m duho.mcp <app>`` subprocess (a StringIO-only test
cannot catch a stray write to the real file descriptor 1, or the process
default text encoding).

Fixtures at module level: AST-based flags/docstring introspection needs a
real source file (same convention as every other ``test_mcp_*.py``).
"""

import io
import json
import os
import subprocess
import sys
import typing as ty

import pytest

from duho import Arg, Cli, Cmd, LoggingArgs, NS
from duho.mcp import (
    InvalidArgumentsError,
    _tree_for,
    call_tool,
    describe_tools,
    serve,
)

# --------------------------------------------------------------------------
# Argv-injection safety (tool arguments are untrusted, LLM-controlled input)
# --------------------------------------------------------------------------


class Rm(Cmd):
    """Remove files."""

    files: "list"
    "Files to remove"
    ("files",)

    force: bool = False
    "Force removal"
    ("--force",)

    def __call__(self):
        return {
            "files": self.files,
            "force": self.force,
            "passthrough": self._passthrough_,
        }


class Note(Cmd):
    """Write a note."""

    text: str
    "Body"
    ("text",)

    title: str = ""
    "Title"
    ("--title",)

    def __call__(self):
        return {"text": self.text, "title": self.title}


class LabelSet(Cmd):
    """Set labels."""

    labels: "dict"
    "key=value labels"
    ("--label",)

    def __call__(self):
        return dict(self.labels)


class InjectRoot(Cli):
    """Root."""

    _subcommands_ = [Rm, Note, LabelSet]


def test_positional_value_that_looks_like_a_flag_is_refused():
    # The value is schema-valid (a list of strings) -- the danger is purely
    # in how argv synthesis would encode it, so this is a runtime isError,
    # not a schema-validation InvalidArgumentsError.
    result = call_tool(InjectRoot, "InjectRoot.Rm", {"files": ["a", "--force"]})
    assert result["isError"] is True
    assert (
        call_tool(InjectRoot, "InjectRoot.Rm", {"files": ["a"], "force": True})[
            "content"
        ][0]["text"]
        != result["content"][0]["text"]
    )


def test_positional_double_dash_does_not_leak_into_passthrough():
    result = call_tool(
        InjectRoot, "InjectRoot.Rm", {"files": ["a", "--"], "force": False}
    )
    assert result["isError"] is True


def test_option_value_starting_with_dash_is_not_reparsed_as_a_flag():
    result = call_tool(InjectRoot, "InjectRoot.Note", {"text": "a", "title": "-x"})
    assert result.get("isError") is not True
    payload = json.loads(result["content"][0]["text"])
    assert payload["title"] == "-x"


def test_option_value_equal_to_double_dash_is_refused():
    result = call_tool(InjectRoot, "InjectRoot.Note", {"text": "a", "title": "--"})
    assert result["isError"] is True


def test_dict_field_key_and_value_survive_verbatim():
    result = call_tool(
        InjectRoot, "InjectRoot.LabelSet", {"labels": {"env": "prod-1", "tier": "-x"}}
    )
    assert result.get("isError") is not True
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"env": "prod-1", "tier": "-x"}


# --------------------------------------------------------------------------
# Bool negation must use a LONG flag (a short-flag-last tuple is common)
# --------------------------------------------------------------------------


class LongFirst(Cmd):
    """Color output."""

    color: bool = True
    "Colorize"
    ("--color", "-c")

    def __call__(self):
        return {"color": self.color}


class ShortOnly(Cmd):
    """Color output, short flag only."""

    color: bool = True
    "Colorize"
    ("-c",)

    def __call__(self):
        return {"color": self.color}


class BoolRoot(Cli):
    """Root."""

    _subcommands_ = [LongFirst, ShortOnly]


def test_false_for_true_default_bool_with_long_flag_last_is_honored():
    result = call_tool(BoolRoot, "BoolRoot.LongFirst", {"color": False})
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {"color": False}


def test_false_for_true_default_bool_with_no_long_flag_is_a_clear_error():
    result = call_tool(BoolRoot, "BoolRoot.ShortOnly", {"color": False})
    assert result["isError"] is True
    assert "long flag" in result["content"][0]["text"]


# --------------------------------------------------------------------------
# Argv synthesis honors the field's EFFECTIVE action (count, Literal[bool])
# --------------------------------------------------------------------------


class Verbosity(LoggingArgs, Cmd):
    """Reports its own effective log level."""

    def __call__(self):
        return {"level": self._logger_.getEffectiveLevel()}


class LitBool(Cmd):
    """A Literal[True, False] field (NOT a bare bool flag)."""

    strict: "ty.Literal[True, False]" = False
    "Strict mode"
    ("--strict",)

    def __call__(self):
        return {"strict": self.strict}


class ActionRoot(Cli):
    """Root."""

    _subcommands_ = [Verbosity, LitBool]


def test_count_action_field_repeats_the_flag():
    import logging

    result = call_tool(ActionRoot, "ActionRoot.Verbosity", {"verbose": 2})
    assert result.get("isError") is not True
    payload = json.loads(result["content"][0]["text"])
    assert payload["level"] <= logging.INFO  # -vv makes it more verbose than default


def test_count_action_field_of_zero_emits_nothing_and_still_succeeds():
    result = call_tool(ActionRoot, "ActionRoot.Verbosity", {"verbose": 0})
    assert result.get("isError") is not True


def test_literal_true_false_field_is_not_forced_through_bare_flag_synthesis():
    result = call_tool(ActionRoot, "ActionRoot.LitBool", {"strict": True})
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {"strict": True}


# --------------------------------------------------------------------------
# Arguments are validated against the tool's own inputSchema
# --------------------------------------------------------------------------


def test_unknown_argument_key_is_rejected():
    with pytest.raises(InvalidArgumentsError, match="unknown argument"):
        call_tool(InjectRoot, "InjectRoot.Note", {"text": "a", "dry-run": True})


def test_wrong_json_type_for_a_boolean_field_is_rejected():
    with pytest.raises(InvalidArgumentsError):
        call_tool(BoolRoot, "BoolRoot.LongFirst", {"color": "false"})


def test_wrong_json_type_for_a_dict_field_is_rejected():
    with pytest.raises(InvalidArgumentsError):
        call_tool(InjectRoot, "InjectRoot.LabelSet", {"labels": "a=b"})


def test_null_argument_means_field_omitted_not_the_string_none():
    class Owner(Cmd):
        """Has an owner field."""

        owner: str = "root"
        "Owner"
        ("--owner",)

        def __call__(self):
            return {"owner": self.owner}

    result = call_tool(Owner, "Owner", {"owner": None})
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"])["owner"] == "root"


# --------------------------------------------------------------------------
# Dispatch goes through the shared root parser: nested tools see root globals
# --------------------------------------------------------------------------


class ProfileRoot(Cli):
    """Root with a global field."""

    profile: str = "default"
    "Active profile"
    ("--profile",)

    _subcommands_ = [Note]


def test_nested_tool_schema_includes_ancestor_fields():
    tools = {t["name"]: t for t in describe_tools(ProfileRoot)}
    note = tools["ProfileRoot.Note"]
    assert "profile" in note["inputSchema"]["properties"]
    assert "text" in note["inputSchema"]["properties"]


def test_nested_tool_call_can_supply_a_root_global():
    result = call_tool(
        ProfileRoot, "ProfileRoot.Note", {"text": "hi", "profile": "staging"}
    )
    assert result.get("isError") is not True


class ReadsRootProfile(Cmd):
    """Reads the root's own field off the shared parsed instance."""

    def __call__(self):
        return {"profile": self.profile}


class ProfileReaderRoot(Cli):
    """Root with a global field a leaf reads back."""

    profile: str = "default"
    "Active profile"
    ("--profile",)

    _subcommands_ = [ReadsRootProfile]


def test_leaf_command_can_read_a_root_field_without_raising():
    result = call_tool(
        ProfileReaderRoot, "ProfileReaderRoot.ReadsRootProfile", {"profile": "prod"}
    )
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {"profile": "prod"}


# --------------------------------------------------------------------------
# Env/config layering reaches an MCP-dispatched command like duho.main
# --------------------------------------------------------------------------


class EnvTool(Cmd):
    """A field satisfiable from the process environment."""

    token: "Arg[str, NS(env='DUHO_MCP_TEST_TOKEN')]"
    "Secret token"
    ("--token",)

    def __call__(self):
        return {"token": self.token}


def test_env_bound_required_field_is_satisfied_without_an_explicit_argument(
    monkeypatch,
):
    monkeypatch.setenv("DUHO_MCP_TEST_TOKEN", "from-env")
    result = call_tool(EnvTool, "EnvTool", {})
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {"token": "from-env"}


def test_env_bound_field_is_dropped_from_required_when_env_is_set(monkeypatch):
    monkeypatch.setenv("DUHO_MCP_TEST_TOKEN", "from-env")
    tools = {t["name"]: t for t in describe_tools(EnvTool)}
    assert "token" not in tools["EnvTool"]["inputSchema"]["required"]


class ConfigTool(Cmd):
    """A field satisfiable from the class's _config_ file."""

    _config_ = None

    region: str = "class-default"
    "Region"
    ("--region",)

    def __call__(self):
        return {"region": self.region}


def test_config_bound_field_is_satisfied_without_an_explicit_argument(tmp_path):
    from duho.mcp import _TREE_CACHE

    cfg = tmp_path / "config_tool.toml"
    cfg.write_text('region = "from-config"\n', encoding="utf-8")
    ConfigTool._config_ = str(cfg)
    _TREE_CACHE.pop(ConfigTool, None)
    try:
        result = call_tool(ConfigTool, "ConfigTool", {})
    finally:
        ConfigTool._config_ = None
        _TREE_CACHE.pop(ConfigTool, None)
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {"region": "from-config"}


# --------------------------------------------------------------------------
# A command's own sys.exit()/SystemExit never kills the server
# --------------------------------------------------------------------------


class ExitsInt(Cmd):
    """Exits with an integer code."""

    def __call__(self):
        print("bye")
        sys.exit(4)


class ExitsZero(Cmd):
    """Exits cleanly."""

    def __call__(self):
        print("done")
        sys.exit(0)


class ExitsMessage(Cmd):
    """Exits with a message."""

    def __call__(self):
        raise SystemExit("config file missing")


class ExitRoot(Cli):
    """Root."""

    _subcommands_ = [ExitsInt, ExitsZero, ExitsMessage]


def test_command_sys_exit_int_becomes_an_error_result_not_a_crash():
    result = call_tool(ExitRoot, "ExitRoot.ExitsInt", {})
    assert result["isError"] is True
    assert "bye" in result["content"][0]["text"]
    assert result["content"][0]["text"].strip().endswith("exit code: 4")


def test_command_sys_exit_zero_is_a_success_result():
    result = call_tool(ExitRoot, "ExitRoot.ExitsZero", {})
    assert result.get("isError") is not True
    assert "done" in result["content"][0]["text"]


def test_command_system_exit_with_message_is_an_error_result():
    result = call_tool(ExitRoot, "ExitRoot.ExitsMessage", {})
    assert result["isError"] is True
    assert "config file missing" in result["content"][0]["text"]


def test_serve_survives_a_command_that_calls_sys_exit():
    stdin = io.StringIO(
        "\n".join(
            json.dumps(r)
            for r in (
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "ExitRoot.ExitsInt", "arguments": {}},
                },
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
        )
        + "\n"
    )
    stdout = io.StringIO()
    rc = serve(ExitRoot, stdin=stdin, stdout=stdout)
    assert rc == 0
    responses = [
        json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()
    ]
    assert [r["id"] for r in responses] == [1, 2]


# --------------------------------------------------------------------------
# JSON-RPC envelope hardening: malformed-but-valid JSON must never crash serve()
# --------------------------------------------------------------------------


class Echo(Cmd):
    """Echoes a value."""

    def __call__(self):
        print("ok")
        return 0


class EchoRoot(Cli):
    """Root."""

    _subcommands_ = [Echo]


def _serve_lines(*raw_lines):
    stdin = io.StringIO("\n".join(raw_lines) + "\n")
    stdout = io.StringIO()
    rc = serve(EchoRoot, stdin=stdin, stdout=stdout)
    responses = [
        json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()
    ]
    return rc, responses


@pytest.mark.parametrize(
    "raw_line",
    [
        "[1, 2]",
        "5",
        '"hello"',
        "null",
        '[{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]',
    ],
)
def test_non_object_json_line_gets_invalid_request_not_a_crash(raw_line):
    ok_request = json.dumps(
        {"jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {}}
    )
    rc, responses = _serve_lines(raw_line, ok_request)
    assert rc == 0
    # The malformed line never answers with a result, and the well-formed
    # request right after it still gets served.
    assert any(r.get("id") == 99 and "result" in r for r in responses)


def test_non_object_params_gets_invalid_params():
    rc, responses = _serve_lines(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1]})
    )
    assert responses[0]["error"]["code"] == -32602


def test_non_string_tool_name_is_reported_not_crashed():
    rc, responses = _serve_lines(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": ["EchoRoot.Echo"], "arguments": {}},
            }
        )
    )
    assert rc == 0
    assert responses[0]["error"]["code"] == -32602


def test_non_object_arguments_is_reported_not_crashed():
    rc, responses = _serve_lines(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "EchoRoot.Echo", "arguments": "word"},
            }
        )
    )
    assert rc == 0
    assert responses[0]["error"]["code"] == -32602


def test_unknown_tool_name_over_json_rpc_is_invalid_params():
    rc, responses = _serve_lines(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "EchoRoot.NoSuchTool", "arguments": {}},
            }
        )
    )
    assert responses[0]["error"]["code"] == -32602


def test_ping_is_answered_with_an_empty_result():
    rc, responses = _serve_lines(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    )
    assert responses[0]["result"] == {}


@pytest.mark.parametrize(
    "requested,expected",
    [
        ("2024-11-05", "2024-11-05"),
        ("2025-03-26", "2025-03-26"),
        ("2099-01-01", "2025-06-18"),
        (None, "2025-06-18"),
    ],
)
def test_initialize_negotiates_a_supported_protocol_version(requested, expected):
    params = {"protocolVersion": requested} if requested else {}
    rc, responses = _serve_lines(
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}
        )
    )
    assert responses[0]["result"]["protocolVersion"] == expected


def test_serverinfo_version_is_duhos_own_version():
    import duho

    rc, responses = _serve_lines(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    )
    assert responses[0]["result"]["serverInfo"]["version"] == duho.__version__


# --------------------------------------------------------------------------
# The command tree is built (and layered) once per root class
# --------------------------------------------------------------------------


def test_tree_is_cached_across_repeated_describe_tools_calls():
    class CacheRoot(Cli):
        """Root."""

        _subcommands_ = [Echo]

    root_parser_1, nodes_1 = _tree_for(CacheRoot)
    root_parser_2, nodes_2 = _tree_for(CacheRoot)
    assert root_parser_1 is root_parser_2
    assert nodes_1 is nodes_2


# --------------------------------------------------------------------------
# python -m duho.mcp <app> end-to-end: real stdio isolation (not just StringIO)
# --------------------------------------------------------------------------


def _run_subprocess_app(
    app_path,
    argv_app_spec,
    request_lines,
    *,
    extra_env=None,
    remove_env=(),
    input_bytes=None,
):
    env = dict(os.environ)
    for key in remove_env:
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    env["PYTHONPATH"] = str(app_path.parent) + os.pathsep + env.get("PYTHONPATH", "")
    stdin_payload = (
        input_bytes
        if input_bytes is not None
        else ("\n".join(json.dumps(r) for r in request_lines) + "\n").encode("utf-8")
    )
    proc = subprocess.run(
        [sys.executable, "-m", "duho.mcp", argv_app_spec],
        input=stdin_payload,
        capture_output=True,
        env=env,
        timeout=30,
    )
    return proc


def test_subprocess_child_output_does_not_corrupt_the_protocol_stream(tmp_path):
    app_file = tmp_path / "noisy_app.py"
    app_file.write_text(
        "import subprocess, sys\n"
        "from duho import Cli, Cmd\n"
        "\n"
        "class Noisy(Cmd):\n"
        '    """Spawns a child that prints without capturing it."""\n'
        "    def __call__(self):\n"
        "        subprocess.run([sys.executable, '-c', \"print('CHILD-STDOUT-LINE')\"])\n"
        '        print("via-stdout")\n'
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """Noisy app."""\n'
        "    _subcommands_ = [Noisy]\n",
        encoding="utf-8",
    )
    proc = _run_subprocess_app(
        app_file,
        "noisy_app:App",
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "App.Noisy", "arguments": {}},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    # Every stdout line must be valid JSON -- the child's stray output must
    # have landed on stderr, never spliced into the protocol stream.
    responses = [json.loads(ln) for ln in lines]
    ids = [r["id"] for r in responses]
    assert ids == [1, 2]
    assert b"CHILD-STDOUT-LINE" in proc.stderr


def test_subprocess_command_reading_stdin_gets_eof_not_the_next_request(tmp_path):
    app_file = tmp_path / "reader_app.py"
    app_file.write_text(
        "from duho import Cli, Cmd\n"
        "\n"
        "class ReadsStdin(Cmd):\n"
        '    """Reads a line from stdin."""\n'
        "    def __call__(self):\n"
        "        import sys\n"
        "        print(repr(sys.stdin.readline()))\n"
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """Reader app."""\n'
        "    _subcommands_ = [ReadsStdin]\n",
        encoding="utf-8",
    )
    proc = _run_subprocess_app(
        app_file,
        "reader_app:App",
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "App.ReadsStdin", "arguments": {}},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    responses = [json.loads(ln) for ln in lines]
    ids = [r["id"] for r in responses]
    # Both requests must still be answered -- the command's stdin read must
    # NOT have consumed the client's next (tools/list) request line.
    assert ids == [1, 2]
    read_text = responses[0]["result"]["content"][0]["text"]
    assert "tools/list" not in read_text


def test_subprocess_non_ascii_argument_round_trips_as_utf8(tmp_path):
    app_file = tmp_path / "utf8_app.py"
    app_file.write_text(
        "from duho import Cli, Cmd\n"
        "\n"
        "class Show(Cmd):\n"
        '    """Shows a value: caf\\u00e9."""\n'
        "    text: str\n"
        '    "Text to show"\n'
        "    ('--text',)\n"
        "\n"
        "    def __call__(self):\n"
        "        print(self.text)\n"
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """UTF-8 app."""\n'
        "    _subcommands_ = [Show]\n",
        encoding="utf-8",
    )
    proc = _run_subprocess_app(
        app_file,
        "utf8_app:App",
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "App.Show", "arguments": {"text": "café"}},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
        remove_env=("PYTHONUTF8", "PYTHONIOENCODING"),
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    responses = [json.loads(ln) for ln in lines]
    assert responses[0]["result"]["content"][0]["text"].strip() == "café"
    # The docstring's own non-ASCII character must not have crashed tools/list.
    tools_result = responses[1]["result"]["tools"]
    assert any("caf" in (t["description"] or "") for t in tools_result)


# --------------------------------------------------------------------------
# ANSI escapes from argparse's native color (3.14+) must never reach MCP text
# --------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 14), reason="argparse color is 3.14+")
def test_argument_error_text_has_no_ansi_escapes_under_force_color(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")

    class NeedsArg(Cmd):
        """Requires a field."""

        required_field: str
        "no default"
        ("--required-field",)

        def __call__(self):  # pragma: no cover
            return 0

    result = call_tool(NeedsArg, "NeedsArg", {})
    assert result["isError"] is True
    assert "\x1b" not in result["content"][0]["text"]


# --------------------------------------------------------------------------
# duho.scaffold's own CLI self-description (it dogfoods duho, same as mcp)
# --------------------------------------------------------------------------


def test_scaffold_cmd_describes_itself_correctly():
    import duho
    from duho.scaffold import ScaffoldCmd

    assert ScaffoldCmd._parsername_ == "duho.scaffold"
    assert ScaffoldCmd._version_ is duho.AUTO
    assert ScaffoldCmd._distribution_ == "duho"


def test_scaffold_reports_a_filesystem_error_cleanly_not_a_traceback(
    tmp_path, capsys, monkeypatch
):
    import duho.scaffold as scaffold_mod

    def _boom(*args, **kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr(scaffold_mod, "generate_launchers", _boom)

    cmd = scaffold_mod.ScaffoldCmd()
    cmd.app = "myapp"
    cmd.root = tmp_path
    cmd.libdir = "lib"
    cmd.python = None
    cmd.force = False

    rc = cmd()
    captured = capsys.readouterr()
    assert rc == 1
    assert "duho.scaffold:" in captured.err
    assert "disk is full" in captured.err
    assert "Traceback" not in captured.err
