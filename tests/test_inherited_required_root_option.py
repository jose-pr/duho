"""A required option shared by a root and a subcommand that inherits it.

The root parser enforces it, so a value given before the subcommand name must
satisfy it; the subcommand's own copy must not require it a second time.
"""

import pytest

import duho
from duho import Cli


class RRoot(Cli):
    token: str
    ("--token",)

    def __call__(self):
        return 0


@RRoot.subcommand
class RSub(RRoot):
    def __call__(self):
        return 0


def test_required_root_option_before_the_subcommand_is_accepted():
    instance = duho.parse(RRoot, ["--token", "X", "r-sub"])
    assert isinstance(instance, RSub)
    assert instance.token == "X"


def test_required_root_option_is_still_enforced():
    with pytest.raises(SystemExit):
        duho.parse(RRoot, ["r-sub"])


def test_required_option_shows_as_required_in_the_subcommand_usage(capsys):
    parser = RRoot._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["r-sub", "-h"])
    usage = capsys.readouterr().out.split("\n\n")[0]
    assert "[--token" not in usage
