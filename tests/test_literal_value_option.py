"""``Meta(literal_value=True)``: the next token is always the option's value."""

import typing as ty

import pytest

import duho
from duho import NS, Arg, Meta


class Plain(duho.Cmd):
    """No literal-value option at all."""

    k: str = "none"
    ("-k", "--k")
    other: str = "none"
    ("--other",)
    names: ty.List[str] = []
    ("--names",)

    def __call__(self):
        return 0


class Lit(duho.Cmd):
    """One literal-value option beside a plain one."""

    k: Arg[str, Meta(literal_value=True)] = "none"
    ("-k", "--k")
    plain: str = "none"
    ("--plain",)
    names: Arg[ty.List[str], NS(literal_value=True)] = []
    ("--names",)
    flag: bool = False
    ("--flag",)

    def __call__(self):
        return 0


class Sub(duho.Cmd):
    """A subcommand with its own literal-value option."""

    token: Arg[str, Meta(literal_value=True)] = "none"
    ("--token",)

    def __call__(self):
        return 0


class Root(duho.Cli):
    _subcommands_ = [Sub]
    verbose: bool = False
    ("-v",)


def _fail(cls, argv, capsys):
    with pytest.raises(SystemExit) as exc:
        duho.parse(cls, argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


# --- what must not change -----------------------------------------------------


def test_without_the_attribute_a_dash_value_is_still_an_error(capsys):
    assert "expected one argument" in _fail(Plain, ["--k", "-x"], capsys)
    assert "expected one argument" in _fail(Plain, ["--k", "--"], capsys)


def test_without_the_attribute_the_tail_and_attached_forms_are_unchanged():
    inst = duho.parse(Plain, ["--k", "v", "--", "a", "-b"])
    assert inst.k == "v" and inst._passthrough_ == ["a", "-b"]
    assert duho.parse(Plain, ["--k=--"]).k == "--"
    assert duho.parse(Plain, ["-k--"]).k == "--"
    inst = duho.parse(Plain, ["--", "--k", "x"])
    assert inst.k == "none" and inst._passthrough_ == ["--k", "x"]


def test_a_plain_option_beside_a_literal_one_is_unchanged(capsys):
    assert "expected one argument" in _fail(Lit, ["--plain", "-x"], capsys)
    assert "expected one argument" in _fail(Lit, ["--plain", "--"], capsys)


# --- the feature --------------------------------------------------------------


def test_double_dash_is_the_value():
    inst = duho.parse(Lit, ["--k", "--"])
    assert inst.k == "--" and inst._passthrough_ == []


def test_a_dash_token_is_the_value():
    assert duho.parse(Lit, ["--k", "-x"]).k == "-x"
    assert duho.parse(Lit, ["--k", "--plain"]).k == "--plain"
    assert duho.parse(Lit, ["--k", "-5"]).k == "-5"


def test_short_flag_joins_its_value():
    assert duho.parse(Lit, ["-k", "--"]).k == "--"
    assert duho.parse(Lit, ["-k", "-x"]).k == "-x"


def test_attached_form_still_works():
    assert duho.parse(Lit, ["--k=--"]).k == "--"
    assert duho.parse(Lit, ["--k=v"]).k == "v"


def test_an_ordinary_value_is_unchanged():
    assert duho.parse(Lit, ["--k", "v"]).k == "v"


def test_the_tail_after_the_value_survives():
    inst = duho.parse(Lit, ["--k", "--", "--", "a", "b"])
    assert inst.k == "--" and inst._passthrough_ == ["a", "b"]
    inst = duho.parse(Lit, ["--k", "v", "--", "x"])
    assert inst.k == "v" and inst._passthrough_ == ["x"]


def test_scanning_stops_at_a_bare_double_dash():
    inst = duho.parse(Lit, ["--", "--k", "x"])
    assert inst.k == "none" and inst._passthrough_ == ["--k", "x"]


def test_a_missing_value_is_still_an_error(capsys):
    assert "expected one argument" in _fail(Lit, ["--k"], capsys)


def test_each_occurrence_of_a_collection_option_joins():
    inst = duho.parse(Lit, ["--names", "-a", "--names", "--"])
    assert inst.names == ["-a", "--"]


def test_other_options_still_parse_around_it():
    inst = duho.parse(Lit, ["--flag", "--k", "--", "--plain", "p"])
    assert inst.flag and inst.k == "--" and inst.plain == "p"


def test_a_subcommand_option_is_found_through_the_tree():
    inst = duho.parse(Root, ["-v", "sub", "--token", "--"])
    assert type(inst) is Sub and inst.token == "--"
    inst = duho.parse(Root, ["sub", "--token", "-x", "--", "tail"])
    assert inst.token == "-x" and inst._passthrough_ == ["tail"]


def test_a_flag_or_positional_cannot_be_literal_value():
    class Bad(duho.Cmd):
        on: Arg[bool, Meta(literal_value=True)] = False

        def __call__(self):
            return 0

    with pytest.raises(ValueError, match="on"):
        Bad._parser_()

    class BadPos(duho.Cmd):
        name: Arg[str, Meta(literal_value=True)]
        ("name",)

        def __call__(self):
            return 0

    with pytest.raises(ValueError, match="name"):
        BadPos._parser_()


def test_env_and_default_are_unaffected(monkeypatch):
    class E(duho.Cmd):
        k: Arg[str, Meta(literal_value=True, env="DUHO_T_K")] = "d"
        ("--k",)

        def __call__(self):
            return 0

    assert duho.parse(E, []).k == "d"
    monkeypatch.setenv("DUHO_T_K", "--")
    assert duho.parse(E, []).k == "--"
