"""Regression tests for A029: an annotation `_factory_for` doesn't recognize
fell through to calling the raw annotation itself on CLI text. `frozenset[str]`
silently split text into characters, `Sequence[str]`/`Iterable[str]` failed
per-value at PARSE time instead of once at build, a PEP 695 `type X = ...`
alias (3.12+) crashed every parser build (`TypeAliasType` isn't callable),
and a `typing.NewType` silently parsed to `str` (it's the identity function
at runtime) on every version.

All classes are declared at module level in this real ``.py`` file so their
AST-derived flags resolve normally.
"""

import sys
import typing as ty

import pytest

import duho
from duho import Args

# --------------------------------------------------------------------------
# frozenset: now routed through the same collection ladder as set (A029
# "optionally route frozenset through the set branch").
# --------------------------------------------------------------------------


class _FrozenSetArgs(Args):
    exts: "frozenset[str]"
    ("--exts",)


def test_frozenset_element_is_one_token_not_characters():
    # Previously the raw `frozenset` builtin was called on each token, and
    # frozenset("py") == frozenset({'p', 'y'}).
    inst = duho.parse(_FrozenSetArgs, ["--exts", "py"])
    assert inst.exts == frozenset({"py"})


class _BareFrozenSetArgs(Args):
    fs: frozenset
    ("--fs",)


def test_bare_frozenset_defaults_to_str_elements():
    inst = duho.parse(_BareFrozenSetArgs, ["--fs", "abc", "--fs", "def"])
    assert inst.fs == frozenset({"abc", "def"})


# --------------------------------------------------------------------------
# An unsupported subscripted generic -> a clear build-time error, not a
# per-value parse-time failure or a silent misparse.
# --------------------------------------------------------------------------


class _SequenceArgs(Args):
    seq: "ty.Sequence[str]"
    ("--seq",)


def test_unsupported_generic_origin_raises_at_build_time():
    with pytest.raises(ValueError) as excinfo:
        _SequenceArgs._parser_()
    msg = str(excinfo.value)
    assert "seq" in msg
    assert "Sequence" in msg


# --------------------------------------------------------------------------
# Annotated nested inside a Union (e.g. Optional[Arg[int, NS(...)]]) -- a
# clear build-time error instead of a bare TypeError from an unhashable-
# metadata dict lookup, or silently dropping the metadata.
# --------------------------------------------------------------------------

from duho import Arg, NS  # noqa: E402


class _NestedAnnotatedArgs(Args):
    n: "ty.Optional[Arg[int, NS(env='ANNOT_N')]]" = None
    ("--n",)


@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="on 3.9, typing.Union.__getitem__ itself eagerly hashes its "
    "members (_remove_dups_flatten -> set(params)) and raises its own "
    "unhashable-Namespace TypeError before duho's _factory_for ever runs "
    "-- not a case duho's ladder can intercept",
)
def test_nested_annotated_in_union_raises_clear_build_time_error():
    with pytest.raises(ValueError) as excinfo:
        _NestedAnnotatedArgs._parser_()
    msg = str(excinfo.value)
    assert "n" in msg
    assert "Annotated" in msg or "Arg[" in msg


# --------------------------------------------------------------------------
# typing.NewType: callable but the identity function at runtime -- must
# dispatch on its __supertype__, not use it as the factory directly.
# --------------------------------------------------------------------------

UserId = ty.NewType("UserId", int)


class _NewTypeArgs(Args):
    uid: "UserId"
    ("--uid",)


def test_newtype_field_converts_to_its_supertype():
    inst = duho.parse(_NewTypeArgs, ["--uid", "5"])
    assert inst.uid == 5
    assert isinstance(inst.uid, int)


# --------------------------------------------------------------------------
# PEP 695 `type X = ...` alias (3.12+ only -- constructed via the
# TypeAliasType constructor directly, never `type X = ...` syntax, so this
# file still parses on the 3.9 floor).
# --------------------------------------------------------------------------


if hasattr(ty, "TypeAliasType"):
    # duho reads field flags/docstrings from class SOURCE via AST, and
    # `typing.get_type_hints` resolves a string annotation against the
    # class's MODULE globals -- both need `Port`/`Names`/the Args subclass
    # itself to be real module-level names, not locals of a test function.
    Port = ty.TypeAliasType("Port", int)
    Names = ty.TypeAliasType("Names", list[str])

    class _Pep695PortArgs(Args):
        port: "Port" = 8080
        ("--port",)

    class _Pep695NamesArgs(Args):
        names: "Names" = None
        ("--name",)


@pytest.mark.skipif(
    not hasattr(ty, "TypeAliasType"), reason="PEP 695 type aliases need 3.12+"
)
def test_pep695_type_alias_scalar_field_converts():
    inst = duho.parse(_Pep695PortArgs, ["--port", "9090"])
    assert inst.port == 9090
    assert isinstance(inst.port, int)


@pytest.mark.skipif(
    not hasattr(ty, "TypeAliasType"), reason="PEP 695 type aliases need 3.12+"
)
def test_pep695_type_alias_list_field_converts():
    inst = duho.parse(_Pep695NamesArgs, ["--name", "a", "--name", "b"])
    assert inst.names == ["a", "b"]
