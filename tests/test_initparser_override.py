"""An `_initparser_` override that accepts and forwards `**kwargs` builds."""

import argparse

import pytest

import duho
from duho import Cmd


class Extra(Cmd):
    x: int = 0

    @classmethod
    def _initparser_(cls, parser, is_subcommand=False, **kwargs):
        # An override may name a keyword it reads; it forwards it by name.
        super()._initparser_(parser, is_subcommand=is_subcommand, **kwargs)
        parser.add_argument("--extra", default="none")
        return parser

    def __call__(self):
        return 0


def test_override_forwarding_kwargs_builds_and_parses():
    parser = Extra._parser_()
    assert parser.parse_known_args(["--extra", "1"])[0].extra == "1"
    assert isinstance(duho.parse(Extra, []), Extra)


def test_the_build_keywords_are_not_accepted_by_position():
    with pytest.raises(TypeError):
        Cmd._initparser_(argparse.ArgumentParser(), True)
