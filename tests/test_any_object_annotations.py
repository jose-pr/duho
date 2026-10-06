"""`Any`/`object` annotations take the text as given; a bare `None`
annotation is refused at build time naming the field."""

from typing import Any, Optional, Union

import pytest

import duho
from duho import Args


def test_any_field_takes_the_text():
    class A(Args):
        x: Any = None

    assert duho.parse(A, ["--x", "v"]).x == "v"


def test_object_field_takes_the_text():
    class O(Args):
        x: object = None

    assert duho.parse(O, ["--x", "v"]).x == "v"


def test_optional_any_field_takes_the_text():
    class OA(Args):
        x: Optional[Any] = None

    assert duho.parse(OA, ["--x", "v"]).x == "v"


def test_any_inside_a_union_takes_the_text():
    class U(Args):
        x: Union[int, Any] = 0

    assert duho.parse(U, ["--x", "7"]).x == 7
    assert duho.parse(U, ["--x", "v"]).x == "v"


def test_none_annotation_is_refused_naming_the_field():
    class N(Args):
        thing: None = None

    with pytest.raises(ValueError, match="thing"):
        duho.parser(N)
