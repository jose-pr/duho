"""Tests for annotations `_factory_for` doesn't recognize, which must not fall
through to calling the raw annotation itself on CLI text: `frozenset[str]`
would split text into characters, `Sequence[str]`/`Iterable[str]` would fail
per-value at PARSE time instead of once at build, a PEP 695 `type X = ...`
alias (3.12+) would crash every parser build (`TypeAliasType` isn't
callable), and a `typing.NewType` would parse to `str` (it's the identity
function at runtime) on every version.

All classes are declared at module level in this real ``.py`` file so their
AST-derived flags resolve normally.
"""

import sys
import typing as ty

import pytest

import duho
from duho import Args

# --------------------------------------------------------------------------
# frozenset: now routed through the same collection ladder as set
# (optionally routing frozenset through the set branch).
# --------------------------------------------------------------------------


class _FrozenSetArgs(Args):
    exts: "frozenset[str]"
    ("--exts",)


def test_frozenset_element_is_one_token_not_characters():
    # The raw `frozenset` builtin must not be called on each token:
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
# Annotated nested inside a Union (e.g. Optional[Arg[int, NS(...)]]): the
# single Annotated member's metadata is lifted out of the Union and applied
# to the field, rather than crashing with a bare TypeError from an
# unhashable-metadata dict lookup or silently dropping the metadata.
# --------------------------------------------------------------------------

from duho import Arg, NS  # noqa: E402


class _NestedAnnotatedArgs(Args):
    n: "ty.Optional[Arg[int, NS(env='ANNOT_N')]]" = None
    ("--n",)


def test_nested_annotated_in_union_lifts_metadata_and_works():
    if sys.version_info < (3, 11):
        # On 3.9 and 3.10, `typing.Union.__getitem__` itself eagerly hashes
        # its members (deduplicating them through a `set`) the moment the
        # annotation is evaluated, before duho's own resolution ever runs --
        # and `Arg[int, NS(...)]`'s `NS(...)` metadata isn't hashable. Not a
        # case duho's ladder can intercept earlier than that; it still
        # surfaces as a clear, field-named error rather than a bare,
        # unattributed one.
        with pytest.raises(TypeError, match="n"):
            _NestedAnnotatedArgs._parser_()
        return
    parser = _NestedAnnotatedArgs._parser_()
    assert parser.parse_args(["--n", "5"]).n == 5
    assert parser.parse_args([]).n is None


def test_nested_annotated_in_union_env_binding_still_applies(monkeypatch):
    if sys.version_info < (3, 11):
        pytest.skip("see test_nested_annotated_in_union_lifts_metadata_and_works")
    monkeypatch.setenv("ANNOT_N", "9")
    inst = duho.parse(_NestedAnnotatedArgs, [])
    assert inst.n == 9


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


# --------------------------------------------------------------------------
# A PEP 695 alias WRAPPING Annotated (`type Port = Annotated[int,
# NS(...)]`) must unwrap through `__value__` -- the bare alias case above
# never carried metadata, so it never exercised this.
# --------------------------------------------------------------------------

if hasattr(ty, "TypeAliasType"):
    PortWithMeta = ty.TypeAliasType(
        "PortWithMeta", ty.Annotated[int, NS(metavar="PORT")]
    )

    class _Pep695AnnotatedAliasArgs(Args):
        port: "PortWithMeta" = 8080
        ("--port",)


@pytest.mark.skipif(
    not hasattr(ty, "TypeAliasType"), reason="PEP 695 type aliases need 3.12+"
)
def test_pep695_type_alias_wrapping_annotated_unwraps_metadata():
    parser = _Pep695AnnotatedAliasArgs._parser_()
    action = next(a for a in parser._actions if a.dest == "port")
    assert action.metavar == "PORT"
    inst = duho.parse(_Pep695AnnotatedAliasArgs, ["--port", "9090"])
    assert inst.port == 9090


# --------------------------------------------------------------------------
# More than one Annotated member inside a Union is ambiguous -- a
# field-named error, never a silent pick of one or a crash naming nobody.
# --------------------------------------------------------------------------


class _AmbiguousUnionArgs(Args):
    n: "ty.Union[Arg[int, NS(metavar='A')], Arg[str, NS(metavar='B')]]" = None
    ("--n",)


@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="on 3.9, typing.Union.__getitem__ itself eagerly hashes its "
    "members before duho's own resolution ever runs (see "
    "test_nested_annotated_in_union_lifts_metadata_and_works)",
)
def test_union_with_two_annotated_members_raises_clear_error():
    with pytest.raises(TypeError, match="n"):
        _AmbiguousUnionArgs._parser_()
