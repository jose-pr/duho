"""A misspelled ``dict`` key is reported once per field, never raised."""

import logging

import duho
from duho import Arg, Argument, ArgumentBuilder, Meta


class WideBuilder(ArgumentBuilder):
    shout: "bool" = False


class Loud(Argument):
    @classmethod
    def _argbuilder_(cls, name, decl, factory=None):
        return WideBuilder(
            name=name,
            flags=(f"--{name}",),
            type=str,
            default=decl.default,
            help="",
        )


def _records(caplog):
    return [r for r in caplog.records if r.name == "duho.args"]


def test_misspelled_key_is_reported_once_and_still_dropped(caplog):
    class Typo(duho.Cmd):
        port: Arg[int, dict(hlep="the port")] = 1
        """the declared help"""

        def __call__(self):
            return 0

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        duho.parser(Typo)
        duho.parser(Typo)
        inst = duho.parse(Typo, ["--port", "3"])
    assert inst.port == 3
    records = _records(caplog)
    assert len(records) == 1
    text = records[0].getMessage()
    for word in ("Typo", "port", "hlep", "help", "metadata key"):
        assert word in text
    assert records[0].levelno == logging.WARNING


def test_documented_keys_and_helpers_are_silent(caplog):
    class Fine(duho.Cmd):
        a: Arg[int, dict(help="x", env="A", group="g")] = 1
        b: Arg[list, duho.Extend(",")] = None
        c: Arg[int, duho.Count()] = 0
        d: Arg[str, duho.Choice("x", "y")] = "x"
        e: Arg[int, Meta(help="m")] = 2

        def __call__(self):
            return 0

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        duho.parser(Fine)
    assert _records(caplog) == []


def test_key_claimed_by_custom_argument_is_silent(caplog):
    class WithCustom(duho.Cmd):
        word: Arg[Loud, dict(shout=True)] = "x"

        def __call__(self):
            return 0

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        duho.parser(WithCustom)
    assert _records(caplog) == []
