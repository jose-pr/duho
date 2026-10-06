"""Sibling subcommand names and aliases clash at build time, naming both classes."""

import pytest

import duho
from duho import Cli, Cmd


class Rm(Cmd):
    _parseraliases_ = ("ls",)

    def __call__(self):
        return 0


class Ls(Cmd):
    def __call__(self):
        return 0


class Build(Cmd):
    def __call__(self):
        return 0


class AliasVsName(Cli):
    _subcommands_ = [Rm, Ls]


class AliasVsNameReversed(Cli):
    _subcommands_ = [Ls, Rm]


class Twice(Cli):
    _subcommands_ = [Build, Build]


class Mv(Cmd):
    _parseraliases_ = ("move", "move")

    def __call__(self):
        return 0


class Cp(Cmd):
    _parseraliases_ = ("move",)

    def __call__(self):
        return 0


class AliasVsAlias(Cli):
    _subcommands_ = [Mv, Cp]


@pytest.mark.parametrize("root", [AliasVsName, AliasVsNameReversed])
def test_alias_equal_to_a_sibling_name_raises_naming_both_classes(root):
    with pytest.raises(ValueError) as caught:
        duho.parser(root)
    message = str(caught.value)
    assert "'ls'" in message and "Rm" in message and "Ls" in message


def test_alias_equal_to_a_sibling_alias_raises_naming_both_classes():
    with pytest.raises(ValueError) as caught:
        duho.parser(AliasVsAlias)
    message = str(caught.value)
    assert "'move'" in message and "Mv" in message and "Cp" in message


def test_class_listed_twice_is_registered_once():
    assert type(duho.parse(Twice, ["build"])) is Build
