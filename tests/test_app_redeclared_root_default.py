"""A command that redeclares a root option's default changes it for that command only."""

import duho
from duho import Cli, Cmd


class Root(Cli):
    region: str = "us"
    ("--region",)


class Eu(Cmd):
    region: str = "eu"
    ("--region",)

    def __call__(self):
        print("region=" + self.region)
        return 0


class Ap(Cmd):
    region: str = "ap"
    ("--region",)

    def __call__(self):
        print("region=" + self.region)
        return 0


class Plain(Cmd):
    def __call__(self):
        print("region=" + self.region)
        return 0


def _region(capsys, commands, argv):
    assert duho.app(Root, commands=commands, argv=argv, setup_logging=False) == 0
    return capsys.readouterr().out.strip()


def test_sibling_without_a_redeclaration_keeps_the_root_default(capsys):
    assert _region(capsys, [Eu, Plain], ["plain"]) == "region=us"
    assert _region(capsys, [Plain, Eu], ["plain"]) == "region=us"


def test_root_value_before_the_subcommand_survives_a_redeclaring_sibling(capsys):
    assert _region(capsys, [Eu, Plain], ["--region", "x", "plain"]) == "region=x"


def test_each_redeclaring_command_gets_its_own_default(capsys):
    assert _region(capsys, [Eu, Ap], ["eu"]) == "region=eu"
    assert _region(capsys, [Eu, Ap], ["ap"]) == "region=ap"


def test_redeclaring_command_still_wins_over_the_root_default(capsys):
    assert _region(capsys, [Eu, Plain], ["eu"]) == "region=eu"
