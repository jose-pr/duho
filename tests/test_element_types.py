"""Tests that a collection ELEMENT type (or dict VALUE type) goes through
`_factory_for` instead of being used as the raw CLI text factory.
`list[Color]` looks enum members up by name (the documented rule), not by
VALUE, `list[date]` reaches `fromisoformat`, and `list[Literal[...]]`
does not always fail. All classes are
declared at module level in this real ``.py`` file so their AST-derived
flags resolve normally.
"""

import datetime
import enum
import typing as ty

import pytest

import duho
from duho import Args


class _Speed(enum.Enum):
    SLOW = 1
    FAST = 2


class _ListEnumArgs(Args):
    speeds: "list[_Speed]"
    ("--speed",)


def test_list_enum_element_matches_by_name():
    inst = duho.parse(_ListEnumArgs, ["--speed", "FAST", "--speed", "SLOW"])
    assert inst.speeds == [_Speed.FAST, _Speed.SLOW]


class _ListDateArgs(Args):
    days: "list[datetime.date]"
    ("--day",)


def test_list_date_element_parses_isoformat():
    inst = duho.parse(_ListDateArgs, ["--day", "2026-01-02"])
    assert inst.days == [datetime.date(2026, 1, 2)]


class _ListLiteralArgs(Args):
    modes: "list[ty.Literal['a', 'b']]"
    ("--mode",)


def test_list_literal_element_accepts_declared_choice():
    inst = duho.parse(_ListLiteralArgs, ["--mode", "a", "--mode", "b"])
    assert inst.modes == ["a", "b"]


def test_list_literal_element_rejects_undeclared_choice():
    with pytest.raises(SystemExit):
        duho.parse(_ListLiteralArgs, ["--mode", "z"])


class _DictEnumArgs(Args):
    m: "dict[str, _Speed]"
    ("--m",)


def test_dict_value_enum_matches_by_name():
    inst = duho.parse(_DictEnumArgs, ["--m", "k=FAST"])
    assert inst.m == {"k": _Speed.FAST}


class _DictDateArgs(Args):
    d: "dict[str, datetime.date]"
    ("--d",)


def test_dict_value_date_parses_isoformat():
    inst = duho.parse(_DictDateArgs, ["--d", "k=2026-01-02"])
    assert inst.d == {"k": datetime.date(2026, 1, 2)}


class _NestedListArgs(Args):
    nested: "list[list[int]]"
    ("--nested",)


def test_nested_collection_element_raises_build_time_error():
    with pytest.raises(ValueError) as excinfo:
        _NestedListArgs._parser_()
    msg = str(excinfo.value)
    assert "nested" in msg
    assert "collection" in msg
