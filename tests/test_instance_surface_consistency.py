"""A parsed instance and a directly constructed one expose the same
attributes: `_passthrough_` is `[]` on every construction path, the private
subcommand dest never stays in `vars()`, and an optional positional gets the
same default either way."""

import argparse
from typing import Optional

import duho
from duho import Arg, Args, Cli, Cmd, Meta


class Build(Cmd):
    target: str = "all"

    def __call__(self):
        return 0


class Root(Cli):
    region: str = "us"
    _subcommands_ = [Build]


def test_direct_cmd_has_an_empty_passthrough():
    assert Build()._passthrough_ == []
    assert Build(target="x")._passthrough_ == []


def test_direct_args_has_an_empty_passthrough_and_it_is_not_shared():
    first, second = Build(), Build()
    first._passthrough_.append("x")
    assert second._passthrough_ == []


def test_parsed_subcommand_does_not_keep_the_private_dest():
    parsed = duho.parse(Root, ["build"])
    assert "_duho_command_" not in vars(parsed)
    assert "_duho_command_" not in repr(parsed)


def test_parsed_subcommand_equals_the_directly_built_one():
    parsed = duho.parse(Root, ["build"])
    assert parsed == Build(target="all", region="us")


def test_finish_parse_returns_the_real_instance():
    root = argparse.ArgumentParser(prog="plain")
    sub = root.add_subparsers()
    Build._parser_(sub, name="build")
    built = duho.finish_parse(root.parse_args(["build"]))
    assert built._passthrough_ == []
    assert "_duho_command_" not in vars(built)


def test_clone_pattern_keeps_the_passthrough():
    parsed = duho.parse(Root, ["build", "--", "a", "b"])
    clone = type(parsed)(**dict(parsed._get_kwargs()))
    assert clone._passthrough_ == ["a", "b"]


class Pos(Args):
    opt: Arg[Optional[int], Meta(flags=("opt",))] = None
    opt2: Arg[Optional[int], Meta(flags=("opt2",))]


def test_optional_positional_default_matches_the_parsed_one():
    parsed = duho.parse(Pos, [])
    direct = Pos()
    assert parsed.opt2 is None
    assert direct.opt2 is None
    assert direct.opt is None
