"""Tests for first-class ``dict[str, V]`` field support (F1).

A ``dict`` field accumulates ``KEY=VALUE`` tokens across repeated flags via
``UpdateAction``; the value half is converted through ``V``. All classes are
declared at module level so the AST-derived flag tuples resolve from a real
file.
"""

import pytest

import duho
from duho import Arg, Args, NS


class DictArgs(Args):
    """Arguments with dict fields."""

    opt: "dict[str, str]" = None
    "String-valued options"
    ("--opt",)

    nums: "dict[str, int]" = None
    "Int-valued options"
    ("--num",)

    bare: dict
    "Bare dict (str->str)"
    ("--bare",)


def test_dict_repeated_flag_merges():
    parser = DictArgs._parser_()
    args = parser.parse_args(["--opt", "k=1", "--opt", "j=2"])
    assert args.opt == {"k": "1", "j": "2"}


def test_dict_value_conversion():
    parser = DictArgs._parser_()
    args = parser.parse_args(["--num", "a=1", "--num", "b=2"])
    assert args.nums == {"a": 1, "b": 2}
    assert all(isinstance(v, int) for v in args.nums.values())


def test_dict_value_with_embedded_equals():
    """Only the first ``=`` splits, so a value may contain ``=``."""
    parser = DictArgs._parser_()
    args = parser.parse_args(["--opt", "url=a=b=c"])
    assert args.opt == {"url": "a=b=c"}


def test_dict_default_empty_when_unset():
    parser = DictArgs._parser_()
    args = parser.parse_args([])
    assert args.bare == {}
    assert isinstance(args.bare, dict)


def test_dict_missing_equals_errors():
    parser = DictArgs._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--opt", "noequals"])


def test_dict_default_not_shared_between_parses():
    """Each parse gets its own dict (copy-on-seed, C7)."""
    p1 = DictArgs._parser_()
    a1 = p1.parse_args(["--opt", "x=1"])
    a2 = DictArgs._parser_().parse_args([])
    assert a1.opt == {"x": "1"}
    assert a2.bare == {}
    # Mutating one must not leak into a fresh parse.
    a1.opt["y"] = "2"
    a3 = DictArgs._parser_().parse_args([])
    assert a3.bare == {}


def test_dict_bare_equivalent_to_str_str():
    parser = DictArgs._parser_()
    args = parser.parse_args(["--bare", "k=v"])
    assert args.bare == {"k": "v"}


class BadKeyArgs(Args):
    """A dict with a non-str key type must error at build time."""

    mapping: "dict[int, str]" = None
    "Bad: int keys"
    ("--mapping",)


def test_dict_non_str_key_errors_at_build():
    with pytest.raises(ValueError, match="key"):
        BadKeyArgs._parser_()


class EnvLayered(Args):
    """Dict field with env layer."""

    labels: Arg["dict[str, str]", NS(env="DUHO_TEST_LABELS")] = None
    "Labels"
    ("--label",)


def test_dict_env_single_pair(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_LABELS", "team=infra")
    result = duho.parse(EnvLayered, [])
    assert result.labels == {"team": "infra"}


class ConfigLayered(Args):
    """Dict field sourced from a TOML table."""

    labels: "dict[str, int]" = None
    "Numeric labels"
    ("--label",)


def test_dict_config_table(tmp_path):
    cfg = tmp_path / "cfg.toml"
    cfg.write_text("[labels]\na = 1\nb = 2\n")
    result = duho.parse(ConfigLayered, [], config=cfg)
    assert result.labels == {"a": 1, "b": 2}
