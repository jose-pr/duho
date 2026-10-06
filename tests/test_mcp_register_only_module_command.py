"""A module command with no declared ``Args`` (a ``register`` hook only) over MCP."""

import pytest

import duho
from duho.mcp import InvalidArgumentsError, _core_for_app, call_tool, describe_tools


class Root(duho.LoggingArgs, duho.Cmd):
    """Root supplying global options."""

    _parsername_ = "root"

    def __call__(self):  # pragma: no cover - root is a namespace
        return 0


_REGISTER_ONLY = '''\
"""Module command whose fields come only from its register hook."""


def register(parser, args):
    parser.add_argument("target")
    parser.add_argument("--cache", action="store_false")
    parser.add_argument("--tag", action="append", default=[])


def main(args):
    print("target=%s cache=%s tags=%s" % (args.target, args.cache, args.tag))
    return 0
'''

_SIBLING = '''\
"""A sibling command."""


def main(args=None):
    return 0
'''


@pytest.fixture
def core(tmp_path):
    (tmp_path / "bare.py").write_text(_REGISTER_ONLY)
    (tmp_path / "other.py").write_text(_SIBLING)
    return _core_for_app(Root, source=tmp_path, argv=[])


def _text(result):
    assert "isError" not in result, result
    return result["content"][0]["text"]


def test_register_only_command_is_listed_with_its_actions_as_properties(core):
    tools = {t["name"]: t for t in describe_tools(core)}
    props = tools["root.bare"]["inputSchema"]["properties"]
    assert {"target", "cache", "tag"} <= set(props)
    assert props["tag"]["type"] == "array"


@pytest.mark.parametrize("given,expected", [(False, "False"), (True, "True")])
def test_store_false_flag_maps_its_boolean_both_ways(core, given, expected):
    out = _text(call_tool(core, "root.bare", {"target": "x", "cache": given}))
    assert "cache=%s" % expected in out


def test_array_option_repeats_the_flag(core):
    out = _text(call_tool(core, "root.bare", {"target": "x", "tag": ["a", "b"]}))
    assert "tags=['a', 'b']" in out


@pytest.mark.parametrize("value", ["--help", "-x", "--", "--no-cache"])
def test_option_shaped_positional_is_refused(core, value):
    with pytest.raises(InvalidArgumentsError):
        call_tool(core, "root.bare", {"target": value})


def test_positional_equal_to_a_sibling_command_name_is_refused(core):
    with pytest.raises(InvalidArgumentsError):
        call_tool(core, "root.bare", {"target": "other"})
