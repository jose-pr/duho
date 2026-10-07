"""The collection/flag helpers (`Append`, `Choice`, `Const`, `Count`,
`Extend`) accept the same metadata keys `Meta` does, and a raw
`kwargs={"help": ...}` overrides the derived help instead of colliding."""

import os
from typing import List

import pytest

import duho
from duho import Arg, Args, Meta


def _help(cls) -> str:
    return cls._parser_().format_help()


def test_append_accepts_help():
    class A(Args):
        tags: Arg[List[str], duho.Append(help="a tag")] = []
        ("--tag",)

    assert "a tag" in _help(A)
    assert duho.parse(A, ["--tag", "x", "--tag", "y"]).tags == ["x", "y"]


def test_choice_accepts_help():
    class C(Args):
        mode: Arg[str, duho.Choice("a", "b", help="the mode")] = "a"

    assert "the mode" in _help(C)
    assert duho.parse(C, ["--mode", "b"]).mode == "b"


def test_const_accepts_help():
    class K(Args):
        fast: Arg[int, duho.Const(1, help="go fast")] = 0

    assert "go fast" in _help(K)
    assert duho.parse(K, ["--fast"]).fast == 1


def test_count_accepts_help_and_flags():
    class V(Args):
        verbose: Arg[int, duho.Count(help="louder", flags=("-v", "--verbose"))] = 0

    assert "louder" in _help(V)
    assert duho.parse(V, ["-vv"]).verbose == 2


def test_extend_accepts_help():
    class E(Args):
        names: Arg[List[str], duho.Extend(",", help="the names")] = []

    assert "the names" in _help(E)
    assert duho.parse(E, ["--names", "a,b"]).names == ["a", "b"]


def test_count_accepts_env_and_group(monkeypatch):
    class V(Args):
        verbose: Arg[int, duho.Count(env="DUHO_T_VERB", group="Output")] = 0

    monkeypatch.setenv("DUHO_T_VERB", "2")
    assert duho.parse(V, []).verbose == 2
    assert "Output" in _help(V)


def test_choice_accepts_conflicts():
    class C(Args):
        a: Arg[str, duho.Choice("x", "y", conflicts="g")] = "x"
        b: Arg[bool, Meta(conflicts="g")] = False

    with pytest.raises(SystemExit):
        duho.parse(C, ["--a", "y", "--b"])


def test_raw_kwargs_help_overrides_derived_help():
    class R(Args):
        n: Arg[int, Meta(kwargs={"help": "raw help"})] = 1
        "derived help"

    text = _help(R)
    assert "raw help" in text
    assert "derived help" not in text
