"""Tests for MCP over an ``app()``-built command tree.

Exercises ``duho.mcp``'s generalized tree walk/schema/dispatch against a
command set resolved the same way ``duho.app(source=...)`` resolves one:
a class command, a module command with its own declared ``Args``, and a
module command with no fields at all. Fixtures are real ``.py`` files under
``tmp_path`` (AST-based introspection needs one; module commands are always
written to disk, never ``-c``, matching ``tests/test_runtime.py``'s own
convention).
"""

import duho
from duho.mcp import _core_for_app, call_tool, describe_tools


class Root(duho.LoggingArgs, duho.Cmd):
    """Root supplying global options for the app-tree MCP tests."""

    _parsername_ = "root"

    def __call__(self):  # pragma: no cover - root is a namespace, never dispatched
        return 0


_CLASS_CMD_DEPLOY = '''\
"""Deploy the thing to an environment."""
from duho import Cmd


class Deploy(Cmd):
    """Deploy the thing to an environment."""

    name: str = "world"
    "Deploy target"
    ("--name",)

    def __call__(self):
        print("deployed", self.name)
        return 0
'''

_MODULE_CMD_GREET = '''\
"""Print a greeting (module command with its own declared Args)."""
from duho import Arg, Meta


class Args:
    name: str = "world"
    "Who to greet"
    ("--name",)

    shout: "Arg[bool, Meta(env='DUHO_TEST_MCP_APP_TREE_SHOUT')]" = False
    "Shout it"
    ("--shout",)


def main(args):
    text = args.name.upper() if args.shout else args.name
    print("hello", text)
    return 0
'''

_MODULE_CMD_PING = '''\
"""A module command with no fields at all."""


def main(args=None):
    print("pong")
    return 0
'''


def _write(dir_path, name, source):
    path = dir_path / name
    path.write_text(source)
    return path


def _build(tmp_path):
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    _write(tmp_path, "greet.py", _MODULE_CMD_GREET)
    _write(tmp_path, "ping.py", _MODULE_CMD_PING)
    return _core_for_app(Root, source=tmp_path, argv=[])


def test_class_and_module_commands_all_listed(tmp_path):
    core = _build(tmp_path)
    tools = {t["name"]: t for t in describe_tools(core)}
    assert set(tools) == {"root.deploy", "root.greet", "root.ping"}


def test_class_command_schema_has_its_own_field(tmp_path):
    core = _build(tmp_path)
    tools = {t["name"]: t for t in describe_tools(core)}
    props = tools["root.deploy"]["inputSchema"]["properties"]
    assert props["name"]["type"] == "string"
    assert props["name"]["default"] == "world"


def test_module_command_with_declared_args_schema(tmp_path):
    core = _build(tmp_path)
    tools = {t["name"]: t for t in describe_tools(core)}
    props = tools["root.greet"]["inputSchema"]["properties"]
    assert props["name"]["type"] == "string"
    assert props["shout"]["type"] == "boolean"


def test_module_command_without_fields_schema_has_only_ancestor_and_passthrough(
    tmp_path,
):
    core = _build(tmp_path)
    tools = {t["name"]: t for t in describe_tools(core)}
    # `ping` itself declares no fields at all -- everything in its schema
    # besides "--" comes from the ROOT ancestor (LoggingArgs' own globals),
    # never something invented for a fieldless module command.
    props = tools["root.ping"]["inputSchema"]["properties"]
    assert set(props) == {"--", "verbose", "quiet", "loglevels"}


def test_class_command_is_callable_over_mcp(tmp_path):
    core = _build(tmp_path)
    result = call_tool(core, "root.deploy", {"name": "prod"})
    assert result.get("isError") is not True
    assert "deployed prod" in result["content"][0]["text"]


def test_module_command_with_declared_args_is_callable_over_mcp(tmp_path):
    core = _build(tmp_path)
    result = call_tool(core, "root.greet", {"name": "duho", "shout": True})
    assert result.get("isError") is not True
    assert "hello DUHO" in result["content"][0]["text"]


def test_module_command_option_accepts_double_dash_value(tmp_path):
    core = _build(tmp_path)
    result = call_tool(core, "root.greet", {"name": "--"})
    assert result.get("isError") is not True
    assert "hello --" in result["content"][0]["text"]


def test_module_command_without_fields_is_callable_over_mcp(tmp_path):
    core = _build(tmp_path)
    result = call_tool(core, "root.ping", {})
    assert result.get("isError") is not True
    assert "pong" in result["content"][0]["text"]


def test_module_command_env_field_still_works_over_cli_layering(tmp_path, monkeypatch):
    # A module command's own Meta(env=...) field still resolves through the
    # real env/config layering pipeline when the MCP call omits it.
    monkeypatch.setenv("DUHO_TEST_MCP_APP_TREE_SHOUT", "1")
    core = _build(tmp_path)
    result = call_tool(core, "root.greet", {"name": "duho"})
    assert result.get("isError") is not True
    assert "hello DUHO" in result["content"][0]["text"]


def test_unknown_module_command_field_rejected(tmp_path):
    core = _build(tmp_path)
    import pytest

    from duho.mcp import InvalidArgumentsError

    with pytest.raises(InvalidArgumentsError):
        call_tool(core, "root.greet", {"bogus": "x"})


def test_root_is_a_namespace_and_not_listed(tmp_path):
    core = _build(tmp_path)
    tools = {t["name"] for t in describe_tools(core)}
    assert "root" not in tools


# --------------------------------------------------------------------------
# Per-command `_mcp_ = False` exclusion -- module commands
# --------------------------------------------------------------------------

_MODULE_CMD_SECRET = '''\
"""A module command opted out of MCP."""

_mcp_ = False


def main(args=None):
    print("leaked secret")
    return 0
'''


def test_module_command_opted_out_is_not_listed_or_callable(tmp_path):
    import pytest

    from duho.mcp import UnknownToolError

    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    _write(tmp_path, "secret.py", _MODULE_CMD_SECRET)
    core = _core_for_app(Root, source=tmp_path, argv=[])

    tools = {t["name"] for t in describe_tools(core)}
    assert tools == {"root.deploy"}
    with pytest.raises(UnknownToolError, match="unknown tool"):
        call_tool(core, "root.secret", {})
