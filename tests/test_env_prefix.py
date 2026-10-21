"""``_env_prefix_``: an environment variable for every field of a class."""

import json
import logging
from typing import Annotated

import pytest

import duho
from duho import Cli, Cmd, ConfigArgs, LoggingArgs, Meta, OutputArgs
from duho.testing import invoke

PREFIXED = (
    "APP_PORT",
    "APP_NAME",
    "APP_OTHER",
    "APP_TAGS",
    "APP_FLAG",
    "APP_VERBOSE",
    "APP_FORMAT",
    "OWN_VAR",
    "COMPUTED_PORT",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in PREFIXED:
        monkeypatch.delenv(var, raising=False)


class Plain(Cmd):
    _env_prefix_ = "APP_"
    port: int = 1
    "The port"
    ("--port",)
    name: str = "base"
    ("--name",)
    other: Annotated[str, Meta(env="OWN_VAR")] = "o"
    ("--other",)
    tags: list[str] = []
    ("--tags",)

    def __call__(self):
        return 0


class Plain2(Plain):
    """Inherits the prefix and the fields."""


class Computed(Cmd):
    _env_prefix_ = staticmethod(lambda cls: cls.__name__.upper() + "_")
    port: int = 1
    ("--port",)


class Unset(Cmd):
    port: int = 1
    ("--port",)


def _builder(cls, name):
    return {b.name: b for b in cls._getargs_()}[name]


def test_string_prefix_gives_every_field_a_name():
    assert [b.env for b in Plain._getargs_()] == [
        "APP_PORT",
        "APP_NAME",
        "OWN_VAR",
        "APP_TAGS",
    ]


def test_explicit_meta_env_wins(monkeypatch):
    monkeypatch.setenv("APP_OTHER", "prefixed")
    monkeypatch.setenv("OWN_VAR", "explicit")
    assert duho.parse(Plain, []).other == "explicit"


def test_value_from_the_variable(monkeypatch):
    monkeypatch.setenv("APP_PORT", "9")
    monkeypatch.setenv("APP_TAGS", "a")
    parsed = duho.parse(Plain, [])
    assert parsed.port == 9 and parsed.tags == ["a"]
    assert duho.value_sources(parsed)["port"] == "env"


def test_cli_beats_the_variable(monkeypatch):
    monkeypatch.setenv("APP_PORT", "9")
    assert duho.parse(Plain, ["--port", "3"]).port == 3


def test_bad_value_is_a_usage_error(monkeypatch):
    monkeypatch.setenv("APP_PORT", "x")
    res = invoke(Plain, [])
    assert res.status == 2 and "APP_PORT" in res.stderr


def test_variable_beats_config_and_class_default(monkeypatch, tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"port": 5, "name": "cfg"}), encoding="utf-8")
    monkeypatch.setenv("APP_PORT", "9")
    parsed = duho.parse(Plain, [], config=cfg)
    assert (parsed.port, parsed.name) == (9, "cfg")
    assert duho.parse(Plain, []).name == "base"


def test_callable_gets_the_command_class(monkeypatch):
    assert _builder(Computed, "port").env == "COMPUTED_PORT"
    monkeypatch.setenv("COMPUTED_PORT", "7")
    assert duho.parse(Computed, []).port == 7


def test_subclass_inherits_the_prefix(monkeypatch):
    monkeypatch.setenv("APP_PORT", "4")
    assert duho.parse(Plain2, []).port == 4

    class Child(Plain):
        extra: int = 0
        ("--extra",)

    assert _builder(Child, "extra").env == "APP_EXTRA"

    class Override(Plain):
        _env_prefix_ = "OTHER_"

    assert _builder(Override, "port").env == "OTHER_PORT"
    assert _builder(Plain, "port").env == "APP_PORT"


def test_class_without_the_attribute_is_unchanged():
    assert _builder(Unset, "port").env is None


def test_none_from_a_callable_means_no_prefix():
    class NoPrefix(Cmd):
        _env_prefix_ = staticmethod(lambda cls: None)
        port: int = 1
        ("--port",)

    assert _builder(NoPrefix, "port").env is None


def test_a_prefix_that_is_not_text_is_a_type_error():
    class Bad(Cmd):
        _env_prefix_ = 3
        port: int = 1
        ("--port",)

    with pytest.raises(TypeError, match="_env_prefix_"):
        Bad._getargs_()


class Presets(LoggingArgs, ConfigArgs, OutputArgs, Cli):
    _parsername_ = "presets-app"
    _env_prefix_ = "APP_"
    _subcommands_ = [Plain]
    flag: bool = False
    ("--flag",)


def test_preset_fields_get_no_env_name():
    envs = {b.name: b.env for b in Presets._getargs_()}
    assert envs["flag"] == "APP_FLAG"
    for name in ("verbose", "quiet", "loglevels", "config", "format", "json"):
        assert envs[name] is None, name


def test_preset_fields_ignore_a_variable_named_like_them(monkeypatch):
    monkeypatch.setenv("APP_VERBOSE", "3")
    monkeypatch.setenv("APP_FORMAT", "json")
    monkeypatch.setenv("APP_FLAG", "1")
    parsed = duho.parse(Presets, ["plain"])
    assert (parsed.verbose, parsed.format, parsed.flag) == (0, "text", True)


class Shown(Plain):
    _help_formatter_ = duho.DefaultsFormatter


def test_help_shows_where_a_value_came_from(monkeypatch, capsys):
    monkeypatch.setenv("APP_PORT", "9")
    with pytest.raises(SystemExit):
        duho.main(Shown, ["--help"])
    out = capsys.readouterr().out
    assert "(from env APP_PORT)" in out and "9)" not in out


def test_misspelled_attribute_is_warned(caplog):
    class Typo(Cmd):
        _env_prefx_ = "A_"
        port: int = 1
        ("--port",)

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        Typo._parser_()
    assert "_env_prefix_" in caplog.text


def test_known_attribute_is_not_warned(caplog):
    with caplog.at_level(logging.WARNING, logger="duho.args"):
        Plain._parser_()
    assert "_env_prefix_" not in caplog.text
