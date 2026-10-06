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
