"""Two pinned behaviours: a field named like its own builtin annotation fails
with an error naming the field, and a list made positional through
`NS(flags=("files",))` alone takes several values."""

from typing import List

import pytest

import duho
from duho import Arg, Args, NS


def test_field_named_like_its_own_type_raises_naming_the_field():
    class Shadowed(Args):
        bool: bool = False

    with pytest.raises(TypeError, match=r"argument 'bool'.*Shadowed"):
        duho.parser(Shadowed)


def test_list_positional_from_ns_flags_alone_takes_every_token():
    class Files(Args):
        files: Arg[List[str], NS(flags=("files",))]

    assert duho.parse(Files, ["f1", "f2", "f3"]).files == ["f1", "f2", "f3"]
