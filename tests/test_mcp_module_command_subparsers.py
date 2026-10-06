"""A module command's own hand-made subparsers are not MCP tools."""

import duho
from duho.mcp import _core_for_app, describe_tools


class Root(duho.LoggingArgs, duho.Cmd):
    """Root supplying global options."""

    _parsername_ = "root"

    def __call__(self):  # pragma: no cover - root is a namespace
        return 0


_NESTED = '''\
"""Module command whose register hook adds hand-made subparsers."""


def register(parser, args):
    parser.add_argument("--top", default="")
    sub = parser.add_subparsers(dest="action")
    add = sub.add_parser("add")
    add.add_argument("--name", default="")
    rm = sub.add_parser("rm")
    rm.add_argument("--name", default="")


def main(args):
    return 0
'''


def test_only_the_module_command_itself_is_listed(tmp_path):
    (tmp_path / "nested.py").write_text(_NESTED)
    core = _core_for_app(Root, source=tmp_path, argv=[])
    assert [t["name"] for t in describe_tools(core)] == ["root.nested"]


def _call(tmp_path, arguments):
    from duho.mcp import call_tool

    (tmp_path / "nested.py").write_text(_NESTED)
    core = _core_for_app(Root, source=tmp_path, argv=[])
    return core, call_tool(core, "root.nested", arguments)


def test_the_module_command_is_callable_without_choosing_a_subparser(tmp_path):
    _core, result = _call(tmp_path, {})
    assert result.get("isError") is not True


def test_the_hook_subparser_is_chosen_by_its_own_property(tmp_path):
    core, result = _call(tmp_path, {"action": "add", "top": "t"})
    assert result.get("isError") is not True
    schema = describe_tools(core)[0]["inputSchema"]
    assert sorted(schema["properties"]["action"]["enum"]) == ["add", "rm"]
    assert "action" not in schema["required"]


def test_a_name_that_is_not_one_of_the_hook_subparsers_is_refused(tmp_path):
    import pytest

    from duho.mcp import InvalidArgumentsError

    with pytest.raises(InvalidArgumentsError):
        _call(tmp_path, {"action": "--top"})
