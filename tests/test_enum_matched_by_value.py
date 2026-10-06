"""``Meta(enum_by="value")`` / ``NS(enum_by="value")`` match an Enum by value text."""

import enum
import typing as ty

import pytest

import duho
import duho.completion as completion
from duho import NS, Arg, Meta
from duho.agenthelp import describe
from duho.mcp._schema import input_schema_for_command


class Mode(enum.Enum):
    FAST = "f"
    SLOW = "s"


class Level(enum.Enum):
    LOW = 1
    HIGH = 2


class ByValue(duho.Cmd):
    mode: Arg[Mode, Meta(enum_by="value")] = Mode.FAST
    """how"""
    level: Arg[Level, NS(enum_by="value")] = Level.LOW
    """how much"""

    def __call__(self):
        return 0


class ByName(duho.Cmd):
    mode: Mode = Mode.FAST
    """how"""

    def __call__(self):
        return 0


def _fail(cls, argv, capsys):
    with pytest.raises(SystemExit) as exc:
        duho.parse(cls, argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


def test_value_text_selects_the_member():
    inst = duho.parse(ByValue, ["--mode", "s", "--level", "2"])
    assert inst.mode is Mode.SLOW
    assert inst.level is Level.HIGH


def test_name_is_rejected_when_matching_by_value(capsys):
    err = _fail(ByValue, ["--mode", "SLOW"], capsys)
    assert "choose from f, s" in err


def test_default_stays_a_member():
    inst = duho.parse(ByValue, [])
    assert inst.mode is Mode.FAST


def test_default_rule_is_unchanged():
    assert duho.parse(ByName, ["--mode", "SLOW"]).mode is Mode.SLOW
    with pytest.raises(SystemExit):
        duho.parse(ByName, ["--mode", "s"])
    assert "{FAST,SLOW}" in ByName._parser_().format_help()


def test_help_lists_the_values():
    assert "{f,s}" in ByValue._parser_().format_help()


def test_env_matches_by_value(monkeypatch):
    class E(duho.Cmd):
        mode: Arg[Mode, Meta(enum_by="value", env="DUHO_T_MODE")] = Mode.FAST

        def __call__(self):
            return 0

    monkeypatch.setenv("DUHO_T_MODE", "s")
    assert duho.parse(E, []).mode is Mode.SLOW


def test_config_matches_by_value_including_a_number(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('mode = "s"\nlevel = 2\n', encoding="utf-8")
    inst = duho.parse(ByValue, [], config=cfg)
    assert inst.mode is Mode.SLOW
    assert inst.level is Level.HIGH


def test_collection_and_optional_members_match_by_value():
    class C(duho.Cmd):
        modes: Arg[ty.List[Mode], Meta(enum_by="value")] = []
        maybe: Arg[ty.Optional[Mode], Meta(enum_by="value")] = None

        def __call__(self):
            return 0

    inst = duho.parse(C, ["--modes", "f", "--modes", "s", "--maybe", "s"])
    assert inst.modes == [Mode.FAST, Mode.SLOW]
    assert inst.maybe is Mode.SLOW


def test_completion_offers_the_values():
    top = completion.spec(ByValue._parser_())
    opt = next(o for o in top.options if "--mode" in o.flags)
    assert opt.choices == ("f", "s")
    level = next(o for o in top.options if "--level" in o.flags)
    assert level.choices == ("1", "2")


def test_agent_help_lists_the_values():
    doc = describe(ByValue)
    opts = {o["dest"]: o for o in doc["options"]}
    assert opts["mode"]["choices"] == ["f", "s"]
    assert opts["mode"]["default"] == "f"
    assert opts["level"]["choices"] == ["1", "2"]


def test_mcp_schema_lists_the_values():
    props = input_schema_for_command(ByValue)["properties"]
    assert props["mode"]["enum"] == ["f", "s"]
    assert props["mode"]["default"] == "f"
    assert props["level"]["enum"] == ["1", "2"]


def test_mcp_schema_default_rule_is_unchanged():
    props = input_schema_for_command(ByName)["properties"]
    assert props["mode"]["enum"] == ["FAST", "SLOW"]
    assert props["mode"]["default"] == "FAST"


def test_aliases_are_not_duplicates():
    class Al(enum.Enum):
        ONE = "1"
        UNO = "1"

    class C(duho.Cmd):
        x: Arg[Al, Meta(enum_by="value")] = Al.ONE

        def __call__(self):
            return 0

    assert duho.parse(C, ["--x", "1"]).x is Al.ONE


def test_two_members_with_the_same_value_text_is_a_build_error():
    class Clash(enum.Enum):
        A = 1
        B = "1"

    class C(duho.Cmd):
        thing: Arg[Clash, Meta(enum_by="value")] = Clash.A

        def __call__(self):
            return 0

    with pytest.raises(ValueError, match="thing"):
        C._parser_()


def test_unknown_enum_by_is_a_build_error():
    class C(duho.Cmd):
        mode: Arg[Mode, Meta(enum_by="values")] = Mode.FAST

        def __call__(self):
            return 0

    with pytest.raises(ValueError, match="mode"):
        C._parser_()


def test_explicit_name_is_todays_rule():
    class C(duho.Cmd):
        mode: Arg[Mode, Meta(enum_by="name")] = Mode.FAST

        def __call__(self):
            return 0

    assert duho.parse(C, ["--mode", "SLOW"]).mode is Mode.SLOW


class Say(duho.Cmd):
    """Print the mode."""

    mode: Arg[Mode, Meta(enum_by="value")] = Mode.FAST
    """how"""

    def __call__(self):
        print("mode", self.mode.name)
        return 0


class Box(duho.Cli):
    """Root."""

    _parsername_ = "box"
    _subcommands_ = [Say]


def test_mcp_call_passes_the_value_text():
    from duho.mcp import InvalidArgumentsError, call_tool

    result = call_tool(Box, "box.say", {"mode": "s"})
    assert result["content"][0]["text"] == "mode SLOW\n"
    with pytest.raises(InvalidArgumentsError):
        call_tool(Box, "box.say", {"mode": "SLOW"})
