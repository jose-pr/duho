"""``duho.app_name``: the one name an application goes by."""

import duho
from duho import Args, Cli, Cmd


class Sub(Cmd):
    _parsername_ = "the-sub"

    def __call__(self):
        print("name=" + duho.app_name(self))
        return 0


class Root(Cli):
    _parsername_ = "named-root"
    _subcommands_ = [Sub]


class Plain(Cmd):
    _parsername_ = "plain-root"

    def __call__(self):
        return 0


class Unnamed(Cmd):
    def __call__(self):
        return 0


def test_a_root_instance_has_the_root_name():
    assert duho.app_name(duho.parse(Plain, [])) == "plain-root"


def test_a_subcommand_instance_has_the_root_name_not_its_own():
    parsed = duho.parse(Root, ["the-sub"])
    assert isinstance(parsed, Sub)
    assert duho.app_name(parsed) == "named-root"


def test_app_name_argument_is_the_name(capsys):
    assert duho.app(Root, commands=[], argv=["the-sub"], name="renamed") == 0
    assert capsys.readouterr().out == "name=renamed\n"


def test_main_runs_with_the_root_name(capsys):
    assert duho.main(Root, ["the-sub"], setup_logging=False) == 0
    assert capsys.readouterr().out == "name=named-root\n"


def test_a_class_has_its_application_name():
    assert duho.app_name(Root) == "named-root"
    # No `_parsername_`: the top-level package, here this test module.
    assert duho.app_name(Unnamed) == __name__


def test_a_directly_built_instance_has_its_class_name():
    assert duho.app_name(Root()) == "named-root"
    assert duho.app_name(Sub()) == "the-sub"


def test_it_is_a_root_export():
    assert "app_name" in duho.__all__ and duho.app_name is duho.args.app_name
    assert duho.app_name(Args) == "args"
