"""A custom `Argument` type keeps its own builder (factory, metavar) when it
is the member of an Optional/Union or the element of a collection."""

from typing import Dict, List, Optional, Union

import duho
from duho import Args, Argument


class Port:
    """Constructor takes an int; command-line text goes through `parse`."""

    def __init__(self, n):
        if not isinstance(n, int):
            raise TypeError("Port needs an int")
        self.n = n

    def __eq__(self, other):
        return isinstance(other, Port) and other.n == self.n

    def __repr__(self):
        return f"Port({self.n})"

    @staticmethod
    def parse(text):
        return Port(int(text))

    @classmethod
    def _argbuilder_(cls, name, decl, factory=None):
        builder = Argument._argbuilder_.__func__(Argument, name, decl, Port.parse)
        builder.type = Port.parse
        builder.metavar = "PORT"
        return builder


def test_top_level_still_works():
    class A(Args):
        p: Port = Port(1)

    assert duho.parse(A, ["--p", "80"]).p == Port(80)


def test_optional_custom_type():
    class A(Args):
        p: Optional[Port] = None

    assert duho.parse(A, ["--p", "80"]).p == Port(80)
    assert "PORT" in A._parser_().format_help()


def test_list_of_custom_type():
    class A(Args):
        p: List[Port] = []

    assert duho.parse(A, ["--p", "80", "--p", "81"]).p == [Port(80), Port(81)]


def test_builtin_list_of_custom_type():
    class A(Args):
        p: list[Port]

    assert duho.parse(A, ["--p", "80"]).p == [Port(80)]


def test_dict_value_custom_type():
    class A(Args):
        p: Dict[str, Port] = {}

    assert duho.parse(A, ["--p", "a=80"]).p == {"a": Port(80)}


def test_union_member_custom_type():
    class A(Args):
        p: Union[Port, str] = "x"

    assert duho.parse(A, ["--p", "80"]).p == Port(80)


def test_env_layer_uses_the_custom_builder(monkeypatch):
    from duho import Arg, NS

    class A(Args):
        p: Arg[Optional[Port], NS(env="DUHO_T_CUSTOM_PORT")] = None

    monkeypatch.setenv("DUHO_T_CUSTOM_PORT", "81")
    assert duho.parse(A, []).p == Port(81)
