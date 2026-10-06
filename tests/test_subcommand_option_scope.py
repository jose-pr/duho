"""An option written after a subcommand name belongs to the subcommand.

A root with an optional positional turns on the argv reorder pass; that pass
must stop at the token that names a subcommand, or it hoists the subcommand's
own options in front of it and the root consumes them.
"""

import json

import duho
from duho import Cli, Cmd
from duho.mcp import call_tool


class Leaf(Cmd):
    name: str = "leaf-default"
    ("--name",)

    out: str = ""
    ("--out",)

    force: bool = False
    ("--force",)

    tag: str = "leaf-default"
    ("--tag",)

    def __call__(self):
        return {
            "name": self.name,
            "out": self.out,
            "force": self.force,
            "tag": self.tag,
        }


class PosRoot(Cli):
    target: str = "."
    ("target",)

    name: str = "root-default"
    ("--name",)

    output_dir: str = ""
    ("--output-dir",)

    force_all: bool = False
    ("--force",)

    tag: str = "root-default"
    ("--tag",)

    _subcommands_ = [Leaf]


def test_option_after_subcommand_name_binds_to_the_subcommand():
    leaf = duho.parse(PosRoot, [".", "leaf", "--name", "x"])
    assert leaf.name == "x"
    leaf = duho.parse(PosRoot, ["leaf", "--name", "x"])
    assert leaf.name == "x"


def test_prefix_option_after_subcommand_name_binds_to_the_subcommand():
    leaf = duho.parse(PosRoot, ["leaf", "--out", "V"])
    assert leaf.out == "V"


def test_flag_after_subcommand_name_does_not_switch_on_the_root_flag():
    leaf = duho.parse(PosRoot, ["leaf", "--force"])
    assert leaf.force is True
    assert leaf.force_all is False


def _call(arguments):
    result = call_tool(PosRoot, __name__.rpartition(".")[2] + ".leaf", arguments)
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def test_mcp_prefix_named_leaf_option_reaches_the_leaf():
    got = _call({"out": "LEAFVALUE"})
    assert got["out"] == "LEAFVALUE"


def test_mcp_same_named_leaf_flag_reaches_the_leaf_only():
    got = _call({"force": True})
    assert got["force"] is True


def test_mcp_same_named_leaf_option_reaches_the_leaf():
    got = _call({"tag": "T", "name": "N"})
    assert got["tag"] == "T"
    assert got["name"] == "N"
