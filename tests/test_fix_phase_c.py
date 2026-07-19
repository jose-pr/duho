"""Phase C regression tests: conversion/build ladder.

C6 (Union recursion), C7 (mutable defaults), C8 (foreign Annotated metadata),
C9 (ClassVar/Final), C10 (Literal of bool), C15 (date/datetime/time factories),
M15 (set flags container), M17 (SUPPRESS anywhere in Annotated metadata).
"""

import argparse
import datetime
import enum
import typing
from typing import List, Literal, Optional, Union

import pytest

import duho
from duho import NS, Arg, Args


# -- C6: recurse Union members through the branch ladder ----------------------


class _OptListInt(Args):
    nums: Optional[List[int]]
    "Numbers"
    ("--nums",)


def test_optional_list_int_multi(monkeypatch):
    r = duho.parse(_OptListInt, ["--nums", "1", "2"])
    assert r.nums == [1, 2]


def test_optional_list_int_single_token():
    # Pre-fix Optional char-splits "123" -> ['1','2','3']; here it is [123].
    r = duho.parse(_OptListInt, ["--nums", "123"])
    assert r.nums == [123]


class _OptLiteral(Args):
    mode: Optional[Literal["fast", "slow"]]
    "Mode"
    ("--mode",)


def test_optional_literal_valid():
    r = duho.parse(_OptLiteral, ["--mode", "fast"])
    assert r.mode == "fast"


def test_optional_literal_invalid():
    with pytest.raises(SystemExit):
        duho.parse(_OptLiteral, ["--mode", "nope"])


class _Color(enum.Enum):
    RED = 1
    BLUE = 2


class _UnionEnumStr(Args):
    val: Union[_Color, str]
    "Value"
    ("--val",)


def test_union_enum_str_enum_by_name():
    r = duho.parse(_UnionEnumStr, ["--val", "RED"])
    assert r.val is _Color.RED


def test_union_enum_str_fallback_str():
    r = duho.parse(_UnionEnumStr, ["--val", "hello"])
    assert r.val == "hello"


def test_union_with_collection_member_is_build_error():
    class _BadUnion(Args):
        x: Union[List[int], str]
        ("--x",)

    with pytest.raises(ValueError):
        _BadUnion._parser_()


# -- C7: copy mutable defaults ------------------------------------------------


class _ListDefault(Args):
    items: List[int]
    "Items"
    ("--items",)


def test_mutable_default_not_shared():
    a = duho.parse(_ListDefault, [])
    a.items.append(99)
    b = duho.parse(_ListDefault, [])
    assert b.items == []
    c = _ListDefault()
    assert c.items == []


# -- C8: tolerate foreign Annotated metadata ----------------------------------


class _AnnotatedDoc(Args):
    n: Arg[int, "a positive int"] = 1
    ("--n",)


def test_foreign_str_metadata_builds_and_parses():
    assert duho.parse(_AnnotatedDoc, ["--n", "5"]).n == 5
    assert duho.parse(_AnnotatedDoc, []).n == 1


class _Doc:
    def __init__(self, documentation):
        self.documentation = documentation


class _PEP727(Args):
    n: Arg[int, _Doc("count of things")] = 1
    ("--n",)


def test_pep727_documentation_sets_help():
    help_text = _PEP727._parser_().format_help()
    assert "count of things" in help_text


# -- C9: skip ClassVar / Final ------------------------------------------------


class _WithClassVar(Args):
    count: typing.ClassVar[int] = 0
    active: bool = False
    "Active"
    ("--active",)


def test_classvar_not_a_flag():
    help_text = _WithClassVar._parser_().format_help()
    assert "--count" not in help_text
    r = duho.parse(_WithClassVar, [])
    assert r.count == 0
    assert r.active is False


# -- C10: Literal of bool ------------------------------------------------------


class _LiteralBool(Args):
    flag: Literal[True, False] = False
    "Flag"
    ("--flag",)


def test_literal_bool_builds_and_roundtrips():
    # Pre-fix: store_true + choices -> argparse TypeError at build.
    parser = _LiteralBool._parser_()
    assert parser is not None
    r = duho.parse(_LiteralBool, ["--flag", "True"])
    assert r.flag is True


# -- C15: date/datetime/time factories ----------------------------------------


class _WhenArgs(Args):
    when: datetime.date
    "When"
    ("--when",)


def test_date_factory():
    r = duho.parse(_WhenArgs, ["--when", "2026-07-19"])
    assert r.when == datetime.date(2026, 7, 19)


def test_date_bad_value_is_argparse_error():
    with pytest.raises(SystemExit):
        duho.parse(_WhenArgs, ["--when", "not-a-date"])


class _OptWhen(Args):
    when: Optional[datetime.datetime]
    "When"
    ("--when",)


def test_optional_datetime_factory():
    r = duho.parse(_OptWhen, ["--when", "2026-07-19T10:30:00"])
    assert r.when == datetime.datetime(2026, 7, 19, 10, 30, 0)


# -- M15: set flags container -------------------------------------------------


def test_set_flags_container_is_build_error():
    class _SetFlags(Args):
        verbose: int = 0
        {"-v"}  # noqa: B018 - deliberate misuse under test

    with pytest.raises(ValueError):
        _SetFlags._parser_()


# -- M17: SUPPRESS honored anywhere in the metadata ---------------------------


class _SuppressSecond(Args):
    hidden: Arg[int, NS(help="x"), argparse.SUPPRESS] = 0
    ("--hidden",)

    shown: int = 1
    "Shown"
    ("--shown",)


def test_suppress_as_second_metadata_item():
    names = {b.name for b in _SuppressSecond._getargs_()}
    assert "hidden" not in names
    assert "shown" in names
