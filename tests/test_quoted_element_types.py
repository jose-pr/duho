"""A quoted member inside a builtin generic (`list["Color"]`,
`dict[str, "int"]`) resolves the same on every supported Python, or fails at
build time naming the field."""

import enum
from typing import Dict, List, Optional

import pytest

import duho
from duho import Args


class Paint(Args):
    colors: list["Color"]
    by_name: dict[str, "Color"]
    counts: dict[str, "int"] = {}
    maybe: Optional[list["Color"]] = None


class Color(enum.Enum):
    RED = 1
    BLUE = 2


def test_quoted_enum_element_in_list():
    assert duho.parse(Paint, ["--colors", "RED"]).colors == [Color.RED]


def test_quoted_enum_value_in_dict():
    assert duho.parse(Paint, ["--colors", "RED", "--by-name", "a=BLUE"]).by_name == {
        "a": Color.BLUE
    }


def test_quoted_builtin_value_in_dict():
    parsed = duho.parse(Paint, ["--colors", "RED", "--counts", "a=5"])
    assert parsed.counts == {"a": 5}


def test_quoted_element_inside_optional():
    parsed = duho.parse(Paint, ["--colors", "RED", "--maybe", "BLUE"])
    assert parsed.maybe == [Color.BLUE]


def test_unresolvable_quoted_element_names_the_field():
    class Broken(Args):
        things: list["NoSuchTypeAnywhere"]

    # 3.11+ resolves the quoted member while reading the hints (a TypeError);
    # 3.9/3.10 reach it at field build (a ValueError). Both name the field.
    with pytest.raises((ValueError, TypeError), match=r"things.*NoSuchTypeAnywhere"):
        duho.parser(Broken)


def test_typing_generics_still_work():
    class Typed(Args):
        colors: List["Color"]
        by_name: Dict[str, "Color"] = {}

    assert duho.parse(Typed, ["--colors", "RED"]).colors == [Color.RED]
