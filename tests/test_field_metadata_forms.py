"""Field metadata is strict as a ``Meta`` and permissive as a ``dict`` or any object with attributes."""

import argparse
import logging

import pytest

import duho
from duho import Arg, Argument, ArgumentBuilder, Cmd, Meta


def _records(caplog):
    return [r for r in caplog.records if r.name == "duho.args"]


def test_ns_is_gone_from_the_package_and_from_args():
    with pytest.raises(ImportError):
        from duho import NS  # noqa: F401
    with pytest.raises(ImportError):
        from duho.args import NS  # noqa: F401, F811
    with pytest.raises(AttributeError):
        duho.NS
    assert "NS" not in duho.__all__
    assert "NS" not in duho.args.__all__


def test_a_namespace_in_field_metadata_is_read_like_a_dict(caplog):
    class Old(Cmd):
        port: Arg[int, argparse.Namespace(help="the port", hlep="oops")] = 1

        def __call__(self):
            return 0

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        parser = duho.parser(Old)
    assert "the port" in parser.format_help()
    (record,) = _records(caplog)
    assert "Old.port" in record.getMessage() and "hlep" in record.getMessage()


class _Marker:
    """An object with attributes: its public ones are read as keys."""

    def __init__(self):
        self.help = "from the marker"
        self.flags = ("--marked",)
        self._private = "not a key"


def test_any_object_with_attributes_is_read_like_a_dict(caplog):
    class Other(Cmd):
        port: Arg[int, _Marker()] = 1

        def __call__(self):
            return 0

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        parser = duho.parser(Other)
    options = [s for a in parser._actions for s in a.option_strings]
    assert "--marked" in options
    assert "from the marker" in parser.format_help()
    assert _records(caplog) == []
    assert duho.parse(Other, ["--marked", "4"]).port == 4


def test_a_class_in_field_metadata_is_not_read(caplog):
    class Other(Cmd):
        port: Arg[int, _Marker] = 1

        def __call__(self):
            return 0

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        parser = duho.parser(Other)
    options = [s for a in parser._actions for s in a.option_strings]
    assert "--port" in options and "--marked" not in options
    assert _records(caplog) == []


def test_a_meta_subclass_stays_strict():
    class Strict(Meta):
        pass

    with pytest.raises(TypeError, match="hlep"):
        Strict(hlep="oops")

    class Typed(Cmd):
        port: Arg[int, Strict(help="typed")] = 1

        def __call__(self):
            return 0

    assert "typed" in duho.parser(Typed).format_help()


def test_a_dict_is_read_and_an_unknown_key_warns_once(caplog):
    class Loose(Cmd):
        port: Arg[int, dict(help="the port", hlep="oops")] = 1

        def __call__(self):
            return 0

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        parser = duho.parser(Loose)
        duho.parser(Loose)
    assert "the port" in parser.format_help()
    (record,) = _records(caplog)
    assert "Loose.port" in record.getMessage() and "hlep" in record.getMessage()


def test_a_dict_literal_is_the_same_form(caplog):
    class Literal(Cmd):
        port: Arg[int, {"help": "a literal", "env": "DUHO_TEST_FORMS_PORT"}] = 1

        def __call__(self):
            return 0

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        parser = duho.parser(Literal)
    assert "a literal" in parser.format_help()
    assert _records(caplog) == []


class _Wide(ArgumentBuilder):
    shout: "bool" = False


class _Loud(Argument):
    @classmethod
    def _argbuilder_(cls, name, decl, factory=None):
        return _Wide(
            name=name,
            flags=(f"--{name}",),
            type=str,
            default=decl.default,
            help="",
        )


def test_a_dict_key_a_custom_builder_declares_reaches_it_silently(caplog):
    class Custom(Cmd):
        word: Arg[_Loud, dict(shout=True)] = "x"

        def __call__(self):
            return 0

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        (built,) = [b for b in Custom._getargs_() if b.name == "word"]
    assert built.shout is True
    assert _records(caplog) == []


def test_meta_and_a_pep727_doc_still_apply():
    class Doc:
        documentation = "documented"

    class Both(Cmd):
        port: Arg[int, Doc(), Meta(env="DUHO_TEST_FORMS_BOTH")] = 1

        def __call__(self):
            return 0

    assert "documented" in duho.parser(Both).format_help()
