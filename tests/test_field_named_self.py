"""A field named `self` parses and constructs like any other."""

import duho
from duho import Args, Cmd


class S(Args):
    self: str = "x"


def test_parse_a_field_named_self():
    assert duho.parse(S, ["--self", "y"]).self == "y"


def test_default_and_constructor():
    assert S().self == "x"
    assert S(self="z").self == "z"


def test_clone_pattern_with_a_field_named_self():
    original = duho.parse(S, ["--self", "y"])
    assert type(original)(**dict(original._get_kwargs())).self == "y"


def test_field_named_self_on_a_subcommand():
    class Run(Cmd):
        self: str = "a"

        def __call__(self):
            return 0

    assert duho.parse(Run, ["--self", "b"]).self == "b"
