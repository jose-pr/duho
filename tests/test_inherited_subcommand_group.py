"""A subcommand group inherits `_subcommands_` the same way nested or as the root."""

import duho
from duho import Cli, Cmd


class Leaf1(Cmd):
    def __call__(self):
        return 0


class BaseGroup(Cmd):
    _subcommands_ = [Leaf1]


class Group2(BaseGroup):
    pass


class Top(Cli):
    _subcommands_ = [Group2]


def test_group_inheriting_subcommands_has_them_as_the_root():
    assert type(duho.parse(Group2, ["leaf1"])) is Leaf1


def test_group_inheriting_subcommands_has_them_when_nested():
    assert type(duho.parse(Top, ["group2", "leaf1"])) is Leaf1
