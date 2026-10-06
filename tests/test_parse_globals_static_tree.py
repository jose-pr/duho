"""`parse_globals` on a static subcommand tree stops at the subcommand name."""

import duho
from duho import Cli, Cmd


class Sub(Cmd):
    output_dir: str = "o"
    ("--output-dir",)

    config: str = "sub-config"
    ("--config",)

    def __call__(self):
        return 0


class GRoot(Cli):
    output: str = "root-default"
    ("--output",)

    config: str = "root-config"
    ("--config",)

    _subcommands_ = [Sub]


def test_prefix_of_a_root_option_after_the_subcommand_is_not_a_global():
    assert duho.parse_globals(GRoot, ["sub", "--out", "x"]).output == "root-default"
    leaf = duho.parse(GRoot, ["sub", "--out", "x"])
    assert leaf.output_dir == "x"


def test_same_named_option_after_the_subcommand_is_not_a_global():
    assert duho.parse_globals(GRoot, ["sub", "--config", "x"]).config == "root-config"


def test_globals_before_the_subcommand_are_still_read():
    got = duho.parse_globals(GRoot, ["--output", "x", "sub", "--config", "y"])
    assert got.output == "x"
    assert got.config == "root-config"


def test_abbreviated_global_before_the_subcommand_is_still_read():
    assert duho.parse_globals(GRoot, ["--out", "x", "sub"]).output == "x"


def test_a_global_value_equal_to_the_subcommand_name_is_a_value():
    assert duho.parse_globals(GRoot, ["--output", "sub", "sub"]).output == "sub"


def test_no_subcommand_given_still_parses():
    assert duho.parse_globals(GRoot, ["--output", "x"]).output == "x"
