"""A class with no readable source keeps its public fields even when one
private annotation cannot be resolved, the same on every Python."""

import sys
import types

import pytest

import duho

_SOURCE = """
from duho import Args

class WithPrivate(Args):
    name: str = "x"
    count: int = 1
    _cache: "OnlyUnderTypeChecking" = None

class Plain(Args):
    name: str = "x"
    count: int = 1
"""


@pytest.fixture
def module():
    mod = types.ModuleType("duho_t_sourceless")  # no __file__: no readable source
    sys.modules["duho_t_sourceless"] = mod
    try:
        exec(_SOURCE, mod.__dict__)
        yield mod
    finally:
        sys.modules.pop("duho_t_sourceless", None)


def _dests(cls):
    return [a.dest for a in duho.parser(cls)._actions if a.dest != "help"]


def test_plain_sourceless_class_keeps_its_fields(module):
    assert _dests(module.Plain) == ["name", "count"]


def test_unresolvable_private_annotation_does_not_drop_fields(module):
    assert _dests(module.WithPrivate) == ["name", "count"]
