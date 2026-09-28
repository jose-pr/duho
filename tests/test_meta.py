"""Tests for duho.Meta -- the typed, typo-safe alternative to NS(...) (F5).

Also covers PEP-727 ``Doc`` duck-typing (an object with a str ``.documentation``
attr contributes help). All classes are declared at module level so AST-derived
flag tuples resolve.
"""

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


def test_meta_kwargs_keeps_its_positional_index_with_default_declared_after_it():
    """`default` was added AFTER `kwargs` already existed; it must sit AFTER
    `kwargs` in the field order too, not before it -- otherwise every
    existing positional `Meta(..., kwargs={...})` call site would have
    silently shifted onto a different field the moment `default` was
    declared earlier in the list."""
    m = Meta(
        None,  # help
        None,  # env
        None,  # conflicts
        None,  # conflicts_required
        None,  # group
        None,  # action
        None,  # nargs
        None,  # const
        None,  # choices
        None,  # metavar
        None,  # required
        None,  # type
        None,  # version
        None,  # flags
        {"foo": "bar"},  # kwargs -- must land here, not on `default`
    )
    assert m.kwargs == {"foo": "bar"}
    assert m.default is _META_UNSET


def test_meta_default_is_the_last_positional_field():
    m = Meta(*([None] * 14), {"a": 1}, "the-default")
    assert m.kwargs == {"a": 1}
    assert m.default == "the-default"


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
    """Meta carries the F2/F3 group metadata too."""

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
# `Meta` is a plain dataclass, so positional construction binds by position.
# A field added after the original design must go LAST, never in the middle
# -- inserting one earlier silently shifts every field declared after it for
# any caller using positional args.
# --------------------------------------------------------------------------


def test_meta_positional_field_order_matches_the_documented_prefix():
    m = Meta(
        "help text",  # help
        "ENVVAR",  # env
        "grp",  # conflicts
        True,  # conflicts_required
        "title",  # group
        "store",  # action
        None,  # nargs
        None,  # const
        (1, 2),  # choices
        "N",  # metavar
        False,  # required
        int,  # type
        "1.0",  # version
        ("-x",),  # flags
    )
    assert m.help == "help text"
    assert m.env == "ENVVAR"
    assert m.conflicts == "grp"
    assert m.conflicts_required is True
    assert m.group == "title"
    assert m.action == "store"
    assert m.choices == (1, 2)
    assert m.metavar == "N"
    assert m.required is False
    assert m.type is int
    assert m.version == "1.0"
    assert m.flags == ("-x",)
    # `default` (added after `flags` took over the removed `dest` slot) sits
    # LAST, right before the `kwargs` escape hatch -- not in the middle of
    # the prefix above.
    assert m.default is duho.args._META_UNSET
