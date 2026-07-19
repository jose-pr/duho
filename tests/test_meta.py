"""Tests for duho.Meta -- the typed, typo-safe alternative to NS(...) (F5).

Also covers PEP-727 ``Doc`` duck-typing (an object with a str ``.documentation``
attr contributes help). All classes are declared at module level so AST-derived
flag tuples resolve.
"""

import pytest

import duho
from duho import Arg, Args, Meta


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
