"""Tests for duho.Meta -- the typed, typo-safe alternative to NS(...).

Also covers PEP-727 ``Doc`` duck-typing (an object with a str ``.documentation``
attr contributes help). All classes are declared at module level so AST-derived
flag tuples resolve.
"""

import inspect

import pytest

import duho
from duho import Arg, Args, Meta
from duho.args import _META_UNSET


class MetaArgs(Args):
    """Fields configured via Meta instead of NS."""

    level: Arg[int, Meta(help="verbosity", env="DUHO_TEST_LEVEL")] = 0
    ("--level",)

    name: Arg[str, Meta(metavar="NAME")] = "x"
    ("--name",)


def test_meta_help_and_env(monkeypatch):
    parser = MetaArgs._parser_()
    help_text = parser.format_help()
    assert "verbosity" in help_text

    monkeypatch.setenv("DUHO_TEST_LEVEL", "7")
    result = duho.parse(MetaArgs, [])
    assert result.level == 7


def test_meta_metavar():
    parser = MetaArgs._parser_()
    help_text = parser.format_help()
    assert "NAME" in help_text


def test_meta_unknown_kwarg_is_type_error():
    """The whole point: a misspelled field is a TypeError, not a silent no-op."""
    with pytest.raises(TypeError):
        Meta(hlep="oops")


def test_meta_only_set_fields_merge():
    """Unset Meta fields (sentinel-valued) must not leak into the builder."""
    m = Meta(help="h")
    opts = m._duho_options_()
    assert opts == {"help": "h"}


def test_meta_dest_is_not_a_field():
    """Meta has no ``dest`` field: a field's dest is always its declared name,

    so a ``dest=`` override that LOOKS honored but is silently dropped
    (``NS(dest=...)``'s behavior) is a loud ``TypeError`` here instead.
    """
    with pytest.raises(TypeError, match="Meta has no 'dest' field"):
        Meta(dest="renamed")


class MetaFlags(Args):
    """Meta(flags=...) is the typed, lint-clean alternative to a bare flag tuple."""

    times: Arg[int, Meta(flags=("-n", "--times"))] = 1


def test_meta_flags():
    result = duho.parse(MetaFlags, ["-n", "3"])
    assert result.times == 3


class MetaDefault(Args):
    """Meta(default=...) works the same as NS(default=...)."""

    count: Arg[int, Meta(default=7)] = 0
    ("--count",)


def test_meta_default():
    result = duho.parse(MetaDefault, [])
    assert result.count == 7


class MetaConflicts(Args):
    """Meta carries the conflict-group metadata too."""

    a: Arg[bool, Meta(conflicts="g", conflicts_required=True)] = False
    ("--a",)

    b: Arg[bool, Meta(conflicts="g")] = False
    ("--b",)


def test_meta_conflicts_required():
    parser = MetaConflicts._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    assert parser.parse_args(["--a"]).a is True


# --- Foreign Annotated metadata is tolerated, not a crash -----------------


class _AnnotatedForeignStr(Args):
    """A bare str in the Annotated metadata (neither NS nor Meta)."""

    n: Arg[int, "a positive int"] = 1
    ("--n",)


def test_foreign_str_metadata_builds_and_parses():
    assert duho.parse(_AnnotatedForeignStr, ["--n", "5"]).n == 5
    assert duho.parse(_AnnotatedForeignStr, []).n == 1


# --- PEP-727 Doc duck-typing --------------------------------------------


class _Doc:
    """Minimal PEP-727-style Doc: exposes a str .documentation attr."""

    def __init__(self, documentation):
        self.documentation = documentation


class DocArgs(Args):
    """A field documented via a PEP-727-style Doc object."""

    count: Arg[int, _Doc("how many")] = 1
    ("--count",)


def test_pep727_documentation_contributes_help():
    parser = DocArgs._parser_()
    assert "how many" in parser.format_help()


# --------------------------------------------------------------------------
# `Meta` takes keyword arguments only.
# --------------------------------------------------------------------------

_FIELDS = (
    "help env conflicts conflicts_required group action nargs const choices "
    "metavar required type version flags kwargs default enum_by literal_value split"
).split()


@pytest.mark.parametrize(
    "args",
    [("help text",), ("-n", "--name"), (None, None, None), ("a", "b", "c", "d")],
)
def test_meta_positional_arguments_are_a_type_error(args):
    with pytest.raises(TypeError, match="keyword arguments only"):
        Meta(*args)


def test_meta_positional_flag_points_at_flags():
    with pytest.raises(TypeError, match=r"flags=\(\.\.\.\)"):
        Meta("-n", "--name")


def test_meta_unknown_keyword_names_it_and_the_closest_field():
    with pytest.raises(TypeError) as info:
        Meta(hlep="oops")
    assert "'hlep'" in str(info.value) and "'help'" in str(info.value)
    with pytest.raises(TypeError) as info:
        Meta(zzzzzz=1)
    assert "'zzzzzz'" in str(info.value) and "did you mean" not in str(info.value)


def test_meta_signature_lists_keyword_only_fields_in_order():
    params = list(inspect.signature(Meta).parameters.values())
    assert [p.name for p in params] == _FIELDS
    assert all(p.kind is p.KEYWORD_ONLY for p in params)
    assert all(p.default is _META_UNSET for p in params)


def test_meta_every_field_is_settable_by_keyword():
    meta = Meta(**{name: name for name in _FIELDS})
    for name in _FIELDS:
        assert getattr(meta, name) == name
    assert Meta().__dict__ == {name: _META_UNSET for name in _FIELDS}


def test_meta_equality_and_repr():
    assert Meta(help="a", env="B") == Meta(env="B", help="a")
    assert Meta(help="a") != Meta(help="b")
    assert repr(Meta(help="a")).startswith("Meta(help='a', env=")


@pytest.mark.parametrize(
    "made",
    [
        duho.Choice("a", "b", help="h"),
        duho.Const(1, help="h"),
        duho.Count(help="h"),
        duho.Append(int, help="h"),
        duho.Extend(",", help="h"),
    ],
)
def test_helpers_return_meta(made):
    assert isinstance(made, Meta)
    assert made.help == "h"


def test_extend_sets_the_split_field():
    assert duho.Extend(",").split("a,b") == ["a", "b"]
