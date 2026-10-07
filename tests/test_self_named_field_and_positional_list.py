"""Fields named like their own builtin annotation, and a list made positional
through `Meta(flags=("files",))` alone."""

from typing import List

import pytest

import duho
from duho import Arg, Args, Meta


def test_field_named_like_its_own_type_builds_from_source():
    class OneBool(Args):
        bool: bool = False

    class OneInt(Args):
        int: int = 0

    assert duho.parse(OneBool, ["--bool"]).bool is True
    assert duho.parse(OneInt, ["--int", "3"]).int == 3


def test_field_named_like_its_own_type_without_source():
    namespace = {"__name__": "duho_t_no_source_module"}
    exec(
        "import duho\nclass Shadowed(duho.Args):\n    bool: bool = False\n",
        namespace,
    )
    # There is no source to recover the intended type from: a clear error
    # naming the field.
    with pytest.raises(TypeError, match=r"argument 'bool'.*Shadowed"):
        duho.parser(namespace["Shadowed"])


def test_list_positional_from_ns_flags_alone_takes_every_token():
    class Files(Args):
        files: Arg[List[str], Meta(flags=("files",))]

    assert duho.parse(Files, ["f1", "f2", "f3"]).files == ["f1", "f2", "f3"]
