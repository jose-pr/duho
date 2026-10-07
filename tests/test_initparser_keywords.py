"""``_initparser_`` takes its build keywords as ``**kwargs`` described by ``InitParserKwargs``."""

import argparse
import inspect
import typing

import pytest

import duho
from duho import Args, Cli, Cmd, InitParserKwargs


def test_the_typed_dict_lists_optional_keys_that_resolve():
    hints = typing.get_type_hints(InitParserKwargs)
    assert set(hints) == {
        "is_subcommand",
        "parent_dests",
        "explicit_prog",
        "agent_root_cls",
        "external_config",
    }
    assert InitParserKwargs.__total__ is False
    assert duho.args.InitParserKwargs is InitParserKwargs


def test_the_hook_takes_the_parser_and_keywords_only():
    params = list(inspect.signature(Args._initparser_).parameters.values())
    assert [p.name for p in params] == ["parser", "kwargs"]
    assert params[1].kind is inspect.Parameter.VAR_KEYWORD
    assert "kwargs" in typing.get_type_hints(Args._initparser_)


def test_every_keyword_the_build_passes_is_listed():
    seen = []

    class Leaf(Cmd):
        def __call__(self):
            return 0

        @classmethod
        def _initparser_(cls, parser, *args, **kwargs):
            seen.append((args, dict(kwargs)))
            return super()._initparser_(parser, *args, **kwargs)

    class Root(Cli):
        _subcommands_ = [Leaf]

        @classmethod
        def _initparser_(cls, parser, *args, **kwargs):
            seen.append((args, dict(kwargs)))
            return super()._initparser_(parser, *args, **kwargs)

    duho.parser(Root)
    assert len(seen) == 2
    for args, kwargs in seen:
        assert args == ()
        assert set(kwargs) <= set(InitParserKwargs.__annotations__)
    assert {kwargs["is_subcommand"] for _, kwargs in seen} == {True, False}


def test_an_override_that_forwards_its_keywords_builds():
    class Tuned(Cmd):
        name: str = "x"

        def __call__(self):
            return 0

        @classmethod
        def _initparser_(cls, parser, **kwargs):
            parser = super()._initparser_(parser, **kwargs)
            parser.epilog = "tuned"
            return parser

    parser = duho.parser(Tuned)
    assert parser.epilog == "tuned"
    assert duho.parse(Tuned, ["--name", "y"]).name == "y"


def test_the_base_refuses_a_keyword_it_does_not_list():
    class Plain(Cmd):
        def __call__(self):
            return 0

    with pytest.raises(TypeError, match="bogus"):
        Plain._initparser_(argparse.ArgumentParser(), bogus=1)


def test_the_base_needs_no_keyword_at_all():
    class Plain(Cmd):
        port: int = 1

        def __call__(self):
            return 0

    parser = Plain._initparser_(argparse.ArgumentParser(prog="plain"))
    assert parser.parse_args(["--port", "3"]).port == 3
