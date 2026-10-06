"""``_default_subcommand_`` names the subcommand used when none is given."""

import pytest

import duho
from duho import NS, Arg


class Resolve(duho.Cmd):
    """Resolve a user."""

    _parseraliases_ = ["r"]
    user: str
    ("user",)
    fingerprint: bool = False
    ("--fingerprint",)

    def __call__(self):
        return 0


class Other(duho.Cmd):
    """Another command."""

    def __call__(self):
        return 0


class Tool(duho.Cli):
    """Tool."""

    _default_subcommand_ = "resolve"
    _subcommands_ = [Resolve, Other]
    verbose: bool = False
    ("-v", "--verbose")
    profile: str = "none"
    ("--profile",)
    multi: "Arg[list[str], NS(nargs='*')]" = []
    ("--multi",)


class Plain(duho.Cli):
    """The same tree without a default."""

    _subcommands_ = [Resolve, Other]
    verbose: bool = False
    ("-v", "--verbose")
    profile: str = "none"
    ("--profile",)


def _fail(cls, argv, capsys):
    with pytest.raises(SystemExit) as exc:
        duho.parse(cls, argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


def test_a_bare_value_selects_the_default():
    inst = duho.parse(Tool, ["bob"])
    assert type(inst) is Resolve
    assert inst.user == "bob"


@pytest.mark.parametrize("argv", [["resolve", "bob"], ["r", "bob"]])
def test_a_registered_name_or_alias_is_left_alone(argv):
    inst = duho.parse(Tool, argv)
    assert type(inst) is Resolve and inst.user == "bob"


def test_another_subcommand_is_left_alone():
    assert type(duho.parse(Tool, ["other"])) is Other


def test_root_options_before_the_value_are_skipped():
    inst = duho.parse(Tool, ["-v", "--profile", "p", "bob"])
    assert type(inst) is Resolve
    assert inst.user == "bob" and inst.verbose is True and inst.profile == "p"


def test_option_value_equal_to_a_subcommand_name_is_a_value():
    inst = duho.parse(Tool, ["--profile", "other", "bob"])
    assert type(inst) is Resolve
    assert inst.profile == "other" and inst.user == "bob"


def test_attached_and_abbreviated_root_options():
    inst = duho.parse(Tool, ["--profile=p", "--verb", "bob"])
    assert type(inst) is Resolve and inst.profile == "p" and inst.verbose


def test_options_of_the_default_after_the_value():
    inst = duho.parse(Tool, ["bob", "--fingerprint"])
    assert type(inst) is Resolve and inst.fingerprint is True


def test_no_token_leaves_the_subcommand_required(capsys):
    assert "required" in _fail(Tool, [], capsys)
    assert "required" in _fail(Tool, ["-v"], capsys)


def test_help_still_works(capsys):
    with pytest.raises(SystemExit) as exc:
        duho.parse(Tool, ["--help"])
    assert exc.value.code == 0
    assert "usage" in capsys.readouterr().out


def test_an_unrecognised_option_first_changes_nothing(capsys):
    err = _fail(Tool, ["--fingerprint", "bob"], capsys)
    assert "unrecognized" in err or "invalid choice" in err


def test_a_variable_arity_root_option_changes_nothing(capsys):
    _fail(Tool, ["--multi", "a", "b"], capsys)


def test_passthrough_tail_is_not_scanned():
    inst = duho.parse(Tool, ["bob", "--", "resolve", "x"])
    assert type(inst) is Resolve
    assert inst._passthrough_ == ["resolve", "x"]


def test_without_the_attribute_a_bare_value_is_still_an_error(capsys):
    assert "invalid choice" in _fail(Plain, ["bob"], capsys)


def test_unknown_default_is_a_build_error_naming_the_class():
    class Bad(duho.Cli):
        _default_subcommand_ = "nope"
        _subcommands_ = [Resolve]

    with pytest.raises(ValueError, match="Bad"):
        Bad._parser_()


def test_default_without_subcommands_is_a_build_error():
    class Lonely(duho.Cli):
        _default_subcommand_ = "resolve"

    with pytest.raises(ValueError, match="Lonely"):
        Lonely._parser_()


def test_nested_group_has_its_own_default():
    class Group(duho.Cmd):
        """Group."""

        _default_subcommand_ = "resolve"
        _subcommands_ = [Resolve]

    class Root(duho.Cli):
        _subcommands_ = [Group, Other]

    inst = duho.parse(Root, ["group", "bob"])
    assert type(inst) is Resolve and inst.user == "bob"
